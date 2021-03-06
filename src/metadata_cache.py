import os
import time
import logging
from typing import Any
from functools import cache
from pathlib import Path
from datetime import datetime

import click
import backoff
import requests

from malexport.exporter.mal_session import MalSession
from malexport.exporter.account import Account
from url_cache.core import URLCache, Summary

from src.common import backoff_handler
from src.paths import metadatacache_dir
from src.log import logger


def _get_img(data: dict) -> str | None:
    if img := data.get("medium"):
        return img
    if img := data.get("large"):
        return img
    return None


@backoff.on_exception(
    lambda: backoff.constant(5),
    requests.exceptions.RequestException,
    max_tries=3,
    on_backoff=backoff_handler,
)
def api_request(session: MalSession, url: str, recursed_times: int = 0) -> Any:
    time.sleep(1)
    resp: requests.Response = session.session.get(url)

    # sometimes 400 happens if the alternative titles are empty
    if resp.status_code == 400 and "alternative_titles," in url:
        if recursed_times > 2:
            resp.raise_for_status()
        logger.warning("trying to remove alternative titles and re-requesting")
        url = url.replace("alternative_titles,", "")
        return api_request(session, url, recursed_times + 1)

    # if token expired, refresh
    if resp.status_code == 401:
        logger.warning("token expired, refreshing")
        refresh_token()
        resp.raise_for_status()

    # if this is an unexpected API failure, and not an expected 404/429/400, wait for a while before retrying
    if resp.status_code == 429:
        logger.warning("API rate limit exceeded, waiting")
        time.sleep(60)
        resp.raise_for_status()

    # for any other error, backoff for a minute and then retry
    # if over 5 times, raise the error
    if (
        recursed_times < 5
        and resp.status_code >= 400
        and resp.status_code not in (404,)
    ):
        click.echo(f"Error {resp.status_code}: {resp.text}", err=True)
        time.sleep(60)
        return api_request(session, url, recursed_times + 1)

    # fallthrough raises error if none of the conditions above match
    resp.raise_for_status()

    # if we get here, we have a successful response
    return resp.json()


@cache
def mal_api_session() -> MalSession:
    assert "MAL_USERNAME" in os.environ
    acc = Account.from_username(os.environ["MAL_USERNAME"])
    acc.mal_api_authenticate()
    assert acc.mal_session is not None
    return acc.mal_session


def refresh_token() -> None:
    mal_api_session().refresh_token()


class MetadataCache(URLCache):

    BASE_URL = "https://api.myanimelist.net/v2/{etype}/{mal_id}?nsfw=true&fields=id,title,main_picture,alternative_titles,start_date,end_date,synopsis,mean,rank,popularity,num_list_users,num_scoring_users,nsfw,created_at,updated_at,media_type,status,genres,my_list_status,num_episodes,start_season,broadcast,source,average_episode_duration,rating,pictures,background,related_anime,related_manga,recommendations,studios,statistics"

    def __init__(
        self, cache_dir: Path = metadatacache_dir, loglevel: int = logging.INFO
    ) -> None:
        self.mal_session = mal_api_session()
        super().__init__(cache_dir=cache_dir, loglevel=loglevel)

    def request_data(self, url: str) -> Summary:
        uurl = self.preprocess_url(url)
        logger.info(f"requesting {uurl}")
        try:
            json_data = api_request(self.mal_session, uurl)
        except requests.exceptions.RequestException as ex:
            logger.exception(f"error requesting {uurl}", exc_info=ex)
            logger.warning(ex.response.text)
            logger.warning(
                "Couldn't cache info, could be deleted or failed to cache because entry data is broken/unapproved causing the MAL API to fail"
            )
            return Summary(
                url=uurl,
                data={},
                metadata={"error": ex.response.status_code},
                timestamp=datetime.now(),
            )
        return Summary(url=uurl, data={}, metadata=json_data, timestamp=datetime.now())

    def refresh_data(self, url: str) -> Summary:
        uurl = self.preprocess_url(url)
        summary = self.request_data(uurl)
        self.summary_cache.put(uurl, summary)
        return summary


def is_404(summary: Summary) -> bool:
    if "error" in summary.metadata:
        return summary.metadata["error"] == 404
    return False


def has_data(summary: Summary) -> bool:
    return all(k in summary.metadata for k in ("title", "id"))


@cache
def metadata_cache() -> MetadataCache:
    return MetadataCache()


def request_metadata(
    id_: str | int,
    entry_type: str,
    /,
    *,
    rerequest_failed: bool = False,
    force_rerequest: bool = False,
    mcache: MetadataCache = metadata_cache(),
) -> Summary:
    assert entry_type in {"anime", "manga"}
    api_url = mcache.__class__.BASE_URL.format(etype=entry_type, mal_id=id_)
    if rerequest_failed:
        sdata = mcache.get(api_url)
        # if theres no data and this isnt a 404, retry
        if not has_data(sdata) and not is_404(sdata):
            logger.info("re-requesting failed entry: {}".format(sdata.metadata))
            return mcache.refresh_data(api_url)
    elif force_rerequest:
        logger.info("re-requesting entry")
        return mcache.refresh_data(api_url)
    return mcache.get(api_url)
