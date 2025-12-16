from time import sleep
import subprocess
from pathlib import Path
from collections import defaultdict
import datetime
from curl_cffi import requests
from curl_cffi.requests.exceptions import HTTPError
from libtorrent import session, parse_magnet_uri, torrent_flags, bencode, bdecode

CONTENT_TYPES = ["series", "movie"]
CINEMATA_URL = "https://v3-cinemeta.strem.io"
TRACKERSLIST_FETCH_URL = (
    "https://cdn.jsdelivr.net/gh/ngosang/trackerslist@master/trackers_best.txt"
)
DHT_ROUTERS = (
    "dht.libtorrent.org:25401",
    "dht.transmissionbt.com:6881",
    "router.bittorrent.com:6881",
    "router.utorrent.com:6881",
    "dht.aelitis.com:6881",
    "router.bt.ouinet.work:6881",
)

base_folder = Path(__file__).parent.resolve()

settings = {"dht_bootstrap_nodes": ",".join(DHT_ROUTERS)}
session_handle = session(settings)
try:
    with open(base_folder / "session.dat", "rb") as session_cache:
        session_handle.load_state(bdecode(session_cache.read()))
except FileNotFoundError:
    pass

today_date_str = str(datetime.date.today())
try:
    with open(base_folder / "tracker_cache", "r+") as tracker_cache_file:
        cache_date = tracker_cache_file.readline().strip()
        cached_trackers = tracker_cache_file.read().splitlines()
        if cache_date == today_date_str:
            bootstrap_trackers = cached_trackers
        else:
            try:
                response = requests.get(TRACKERSLIST_FETCH_URL)
                response.raise_for_status()
                raw_trackers = response.text
            except HTTPError:
                bootstrap_trackers = cached_trackers
            else:
                tracker_cache_file.seek(0)
                tracker_cache_file.write(today_date_str + "\n" + raw_trackers)
                bootstrap_trackers = raw_trackers.split()
except FileNotFoundError:
    with open(base_folder / "tracker_cache", "w") as tracker_cache_file:
        try:
            response = requests.get(TRACKERSLIST_FETCH_URL)
            response.raise_for_status()
            raw_trackers = response.text
        except HTTPError:
            bootstrap_trackers = []
        else:
            tracker_cache_file.write(today_date_str + "\n" + raw_trackers)
            bootstrap_trackers = raw_trackers.split()

search = input()

catalog = {}
for content_type in CONTENT_TYPES:
    entries_in_type = requests.get(
        f"{CINEMATA_URL}/catalog/{content_type}/top/search={search}.json"
    ).json()["metas"]
    catalog[content_type] = entries_in_type
    print("=" * 5, content_type.upper(), "=" * 5)
    for index, entry in enumerate(entries_in_type):
        print(f"{index} - {entry['name']}")
    print("-" * 20)
    print()

catalog_selected_type = catalog[CONTENT_TYPES[int(input())]]
selected_entry = catalog_selected_type[int(input())]
item_id = selected_entry["imdb_id"]

metadata = requests.get(
    f"{CINEMATA_URL}/meta/{selected_entry['type']}/{item_id}.json"
).json()["meta"]

print(metadata["description"])

if selected_entry["type"] == "series":
    seasons = defaultdict(list)
    for video in metadata["videos"]:
        seasons[video["season"]].append(video)
    selected_season = int(input("select a season number:"))
    episodes = seasons[selected_season]
    for episode in episodes:
        print(episode["name"])
        print(episode["overview"])
    selected_episode = int(input("select an episode: "))
    item_id += f":{selected_season}:{selected_episode}"

available_streams = requests.get(
    f"https://torrentio.strem.fun/stream/{selected_entry['type']}/{item_id}.json"
).json()["streams"]
for index, entry in enumerate(available_streams):
    print(f"{index} - {entry['title']}")
selected_stream = available_streams[int(input())]


magnet_link = f"magnet:?xt=urn:btih:{selected_stream['infoHash']}"

current_torrent_trackers = bootstrap_trackers.copy()
for source in selected_stream.get("sources", []):
    if source.startswith("tracker:"):
        current_torrent_trackers.append(source.lstrip("tracker:"))

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
for i in range(num_files):
    if files.file_name(i) == selected_stream["behaviorHints"]["filename"]:
        wanted_file_index = i
        wanted_file_extension = Path(files.file_path(i)).suffix
        priorities[i] = 1
        break

stream_buffer_path = base_folder / f"stream_buffer{wanted_file_extension}"
torrent_handle.prioritize_files(priorities)
torrent_handle.rename_file(wanted_file_index, str(stream_buffer_path))
torrent_handle.unset_flags(torrent_flags.upload_mode)

while torrent_handle.status().total_download < 1024 * 1024 * 50:
    sleep(1)
player_process = subprocess.Popen(["mpv", stream_buffer_path, "--keep-open"])

player_process.wait()
with open("session.dat", "wb") as session_state_savefile:
    session_state_savefile.write(bencode(session_handle.save_state()))
stream_buffer_path.unlink()
