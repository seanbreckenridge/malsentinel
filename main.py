import logging

import orjson
import click

from src.metadata_cache import request_metadata
from src.linear_history import track_diffs, read_linear_history
from src.ids import approved_ids, unapproved_ids, estimate_all_users_max
from src.index_requests import request_pages, currently_requesting, queue


@click.group()
@click.option("--debug", is_flag=True, help="Enable debug logs")
def main(debug: bool) -> None:
    if debug:
        import src.log

        src.log.logger = src.log.setup(level=logging.DEBUG)


@main.command(short_help="create timeline using git history")
def linear_history() -> None:
    """Create a big json file with dates based on the git timestamps for when entries were added to cache"""
    for d in track_diffs():
        print(orjson.dumps(d).decode("utf-8"))


@main.command(short_help="request missing data using API")
@click.option("--request-failed", is_flag=True, help="re-request failed entries")
def update_metadata(request_failed: bool) -> None:
    """
    request missing entry metadata using MAL API
    """
    for hs in read_linear_history():
        request_metadata(hs["entry_id"], hs["e_type"], rerequest_failed=request_failed)

    unapproved = unapproved_ids()
    for aid in unapproved.anime:
        request_metadata(aid, "anime", rerequest_failed=request_failed)

    for mid in unapproved.manga:
        request_metadata(mid, "manga", rerequest_failed=request_failed)


@main.command(short_help="print approved/unapproved counts")
def counts() -> None:
    """
    print approved/unapproved counts for anime/manga
    """
    a = approved_ids()
    u = unapproved_ids()
    click.echo(f"Approved anime: {len(a.anime)}")
    click.echo(f"Approved manga: {len(a.manga)}")
    click.echo(f"Unapproved anime: {len(u.anime)}")
    click.echo(f"Unapproved manga: {len(u.manga)}")


@main.command(short_help="print page ranges from indexer")
def pages() -> None:
    """
    print page ranges from indexer
    """
    click.echo("currently requesting: {}".format(currently_requesting()))
    click.echo("queue: {}".format(queue()))


@main.command(short_help="use user lists to find out if new entries have been approved")
@click.option("--list-type", type=click.Choice(["anime", "manga"]), default="anime")
@click.option("--request", is_flag=True, help="request new entries")
@click.argument("USERNAMES", type=click.Path(exists=True))
def estimate_user_recent(usernames: str, request: bool, list_type: str) -> None:
    check_usernames: list[str] = []
    with open(usernames, "r") as f:
        for line in f:
            check_usernames.append(line.strip())
    assert len(check_usernames) > 0
    check_pages = estimate_all_users_max(check_usernames, list_type)
    click.echo(f"should check {check_pages} {list_type} pages".format(check_pages))
    if request:
        if check_pages == 0:
            click.echo("no new entries found, skipping request")
            return
        request_pages(list_type, check_pages)


if __name__ == "__main__":
    main(prog_name="generate_history")
