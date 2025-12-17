from time import sleep
import subprocess
from pathlib import Path
from collections import defaultdict
import datetime
from curl_cffi import requests
from curl_cffi.requests.exceptions import HTTPError
from libtorrent import session, parse_magnet_uri, torrent_flags, bencode, bdecode

CONTENT_TYPES = ["series", "movie"]
CATALOG_PROVIDER_URL = "https://v3-cinemeta.strem.io"
TRACKERSLIST_FETCH_URL = (
    "https://cdn.jsdelivr.net/gh/ngosang/trackerslist@master/trackers_best.txt"
)
STREAM_PROVIDERS_URL = ("https://torrentio.strem.fun",)
DHT_ROUTERS = (
    "dht.libtorrent.org:25401",
    "dht.transmissionbt.com:6881",
    "router.bittorrent.com:6881",
    "router.utorrent.com:6881",
    "dht.aelitis.com:6881",
    "router.bt.ouinet.work:6881",
)
BASE_FOLDER = Path(__file__).parent.resolve()


def get_bootstrap_trackers():
    today_date_str = str(datetime.date.today())

    try:
        with open(BASE_FOLDER / "tracker_cache", "r+") as tracker_cache_file:
            cache_date = tracker_cache_file.readline().strip()
            cached_trackers = tracker_cache_file.read().splitlines()
            if cache_date == today_date_str:
                return cached_trackers
            else:
                try:
                    response = requests.get(TRACKERSLIST_FETCH_URL)
                    response.raise_for_status()
                    raw_trackers = response.text
                except HTTPError:
                    return cached_trackers
                else:
                    tracker_cache_file.seek(0)
                    tracker_cache_file.write(today_date_str + "\n" + raw_trackers)
                    return raw_trackers.split()
    except FileNotFoundError:
        with open(BASE_FOLDER / "tracker_cache", "w") as tracker_cache_file:
            try:
                response = requests.get(TRACKERSLIST_FETCH_URL)
                response.raise_for_status()
                raw_trackers = response.text
            except HTTPError:
                return []
            else:
                tracker_cache_file.write(today_date_str + "\n" + raw_trackers)
                return raw_trackers.split()


def get_session_handle():
    settings = {"dht_bootstrap_nodes": ",".join(DHT_ROUTERS)}

    session_handle = session(settings)
    session_handle.bootstrap_trackers = get_bootstrap_trackers()
    try:
        with open(BASE_FOLDER / "session.dat", "rb") as session_cache:
            session_handle.load_state(bdecode(session_cache.read()))
    except FileNotFoundError:
        pass

    return session_handle


def search_catalog(query):
    catalog = {}

    for content_type in CONTENT_TYPES:
        entries_in_type = requests.get(
            f"{CATALOG_PROVIDER_URL}/catalog/{content_type}/top/search={query}.json"
        ).json()["metas"]
        catalog[content_type] = entries_in_type

    return catalog


def get_metadata(entry):
    metadata = requests.get(
        f"{CATALOG_PROVIDER_URL}/meta/{entry['type']}/{entry['imdb_id']}.json"
    ).json()["meta"]

    if entry["type"] == "series":
        metadata["seasons"] = defaultdict(list)
        for video in metadata["videos"]:
            metadata["seasons"][video["season"]].append(video)

    return metadata


def get_available_streams(entry_data):
    if entry_data["type"] == "series":
        item_id = ":".join(
            [
                entry_data["imdb_id"],
                entry_data.get("selected_season", ""),
                entry_data.get("selected_episode", ""),
            ]
        )
    else:
        item_id = entry_data["imdb_id"]

    available_streams = []
    for stream_provider in STREAM_PROVIDERS_URL:
        available_streams.extend(
            requests.get(
                f"{stream_provider}/stream/{entry_data['type']}/{item_id}.json"
            ).json()["streams"]
        )

    return available_streams


def start_download(session_handle: session, stream_data):
    magnet_link = f"magnet:?xt=urn:btih:{stream_data['infoHash']}"

    current_torrent_trackers = session_handle.bootstrap_trackers.copy()
    for source in stream_data.get("sources", []):
        if source.startswith("tracker:"):
            current_torrent_trackers.append(source.lstrip("tracker:"))
        if not source.startswith("dht:"):
            current_torrent_trackers.append(source)

    params = parse_magnet_uri(magnet_link)
    params.save_path = "."
    params.flags |= torrent_flags.sequential_download
    params.flags |= torrent_flags.upload_mode
    params.trackers = current_torrent_trackers

    torrent_handle = session_handle.add_torrent(params)

    print("Downloading Metadata...")
    while not torrent_handle.status().has_metadata:
        sleep(1)
        print(f"Peers: {torrent_handle.status().num_peers}")

    torrent_info = torrent_handle.torrent_file()
    num_files = torrent_info.num_files()
    files = torrent_info.files()
    priorities = [0] * num_files
    stream_file_index = stream_data["fileIdx"]
    priorities[stream_file_index] = 1
    stream_file_extension = Path(stream_data["behaviorHints"]["filename"]).suffix

    stream_buffer_file = BASE_FOLDER / f"stream_buffer{stream_file_extension}"
    if stream_buffer_file.exists():
        stream_buffer_file.unlink()

    torrent_handle.prioritize_files(priorities)
    torrent_handle.rename_file(stream_file_index, str(stream_buffer_file))
    torrent_handle.unset_flags(torrent_flags.upload_mode)

    while torrent_handle.status().total_download < 1024 * 1024 * 50:
        print(torrent_handle.status().total_download)
        sleep(1)

    player_process = subprocess.Popen(["mpv", stream_buffer_file, "--keep-open"])
    player_process.wait()
    session_handle.remove_torrent(torrent_handle)
    stream_buffer_file.unlink()


def close_session(session_handle: session):
    with open("session.dat", "wb") as session_state_savefile:
        session_state_savefile.write(bencode(session_handle.save_state()))
