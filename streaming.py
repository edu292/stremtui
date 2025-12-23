import datetime
import subprocess
from pathlib import Path
from time import sleep

import curl_cffi
from aiofiles import open
from curl_cffi.requests.exceptions import HTTPError
from httpx import AsyncClient
from libtorrent import bdecode, bencode, options_t, parse_magnet_uri, session, torrent_flags

CATALOG_PROVIDER_URL = 'https://v3-cinemeta.strem.io'
METADATA_PROVIDER_URL = 'https://v3-cinemeta.strem.io'
TRACKERSLIST_FETCH_URL = 'https://cdn.jsdelivr.net/gh/ngosang/trackerslist@master/trackers_best.txt'
STREAM_PROVIDERS_URL = ('https://torrentio.strem.fun',)
DHT_ROUTERS = (
    'dht.libtorrent.org:25401',
    'dht.transmissionbt.com:6881',
    'router.bittorrent.com:6881',
    'router.utorrent.com:6881',
    'dht.aelitis.com:6881',
    'router.bt.ouinet.work:6881',
)
BASE_FOLDER = Path(__file__).parent.resolve()
CURL_CFFI_CLIENT = curl_cffi.AsyncSession()


async def get_bootstrap_trackers(client: AsyncClient) -> list[str]:
    today_date_str = str(datetime.date.today())

    try:
        async with open(BASE_FOLDER / 'tracker_cache', 'r+') as tracker_cache_file:
            cache_date = await tracker_cache_file.readline()
            cache_date = cache_date.strip()
            cached_trackers = await tracker_cache_file.read()
            cached_trackers = cached_trackers.splitlines()
            if cache_date == today_date_str:
                return cached_trackers
            else:
                try:
                    response = await client.get(TRACKERSLIST_FETCH_URL)
                    response.raise_for_status()
                    raw_trackers = response.text
                except HTTPError:
                    return cached_trackers
                else:
                    await tracker_cache_file.seek(0)
                    await tracker_cache_file.write(today_date_str + '\n' + raw_trackers)
                    return raw_trackers.split()
    except FileNotFoundError:
        async with open(BASE_FOLDER / 'tracker_cache', 'w') as tracker_cache_file:
            try:
                response = await client.get(TRACKERSLIST_FETCH_URL)
                response.raise_for_status()
                raw_trackers = response.text
            except HTTPError:
                return []
            else:
                await tracker_cache_file.write(today_date_str + '\n' + raw_trackers)
                return raw_trackers.split()


async def get_session_handle(client: AsyncClient) -> session:
    settings = {'dht_bootstrap_nodes': ','.join(DHT_ROUTERS)}

    session_handle = session(settings)
    session_handle.bootstrap_trackers = await get_bootstrap_trackers(client)
    try:
        async with open(BASE_FOLDER / 'session.dat', 'rb') as session_cache_file:
            session_cache = await session_cache_file.read()
            session_handle.load_state(bdecode(session_cache))
    except FileNotFoundError:
        pass

    return session_handle


def search_catalog(client: AsyncClient, query: str):
    async def task(client: AsyncClient, query: str, content_type: str):
        response = await client.get(f'{CATALOG_PROVIDER_URL}/catalog/{content_type}/top/search={query}.json')
        return content_type, response.json()['metas']

    tasks = [task(client, query, content_type) for content_type in CONTENT_TYPES]

    return tasks


async def get_metadata(client: AsyncClient, entry: dict[str, str]) -> dict[str, str]:
    response = await client.get(f'{METADATA_PROVIDER_URL}/meta/{entry["type"]}/{entry["imdb_id"]}.json')
    metadata = response.json()['meta']

    if entry['type'] == 'series':
        seasons_data = []
        for video in metadata['videos']:
            season = video['season']
            while len(seasons_data) < season + 1:
                seasons_data.append([])

            seasons_data[season].append(video)
        metadata['seasons_data'] = seasons_data

    return metadata


def get_available_streams(item_id, item_type):
    async def task(stream_provider):
        response = await CURL_CFFI_CLIENT.get(f'{stream_provider}/stream/{item_type}/{item_id}.json')
        return response.json()['streams']

    tasks = [task(stream_provider) for stream_provider in STREAM_PROVIDERS_URL]

    return tasks


def start_download(session_handle: session, stream_data):
    magnet_link = f'magnet:?xt=urn:btih:{stream_data["infoHash"]}'

    current_torrent_trackers = session_handle.bootstrap_trackers.copy()
    for source in stream_data.get('sources', []):
        if source.startswith('tracker:'):
            current_torrent_trackers.append(source.lstrip('tracker:'))
        if not source.startswith('dht:'):
            current_torrent_trackers.append(source)

    params = parse_magnet_uri(magnet_link)
    params.save_path = '.'
    params.flags |= torrent_flags.sequential_download
    params.flags |= torrent_flags.upload_mode
    params.trackers = current_torrent_trackers

    torrent_handle = session_handle.add_torrent(params)

    print('Downloading Metadata...')
    while not torrent_handle.status().has_metadata:
        sleep(1)
        print(f'Peers: {torrent_handle.status().num_peers}')

    torrent_info = torrent_handle.torrent_file()
    num_files = torrent_info.num_files()
    priorities = [0] * num_files
    stream_file_index = stream_data['fileIdx']
    priorities[stream_file_index] = 1
    stream_file_extension = Path(stream_data['behaviorHints']['filename']).suffix

    stream_buffer_file = BASE_FOLDER / f'stream_buffer{stream_file_extension}'
    if stream_buffer_file.exists():
        stream_buffer_file.unlink()

    torrent_handle.prioritize_files(priorities)
    torrent_handle.rename_file(stream_file_index, str(stream_buffer_file))
    torrent_handle.unset_flags(torrent_flags.upload_mode)

    while torrent_handle.status().total_download < 1024 * 1024 * 50:
        print(torrent_handle.status().total_download)
        sleep(1)

    player_process = subprocess.Popen(['mpv', stream_buffer_file, '--keep-open'])
    player_process.wait()
    session_handle.remove_torrent(torrent_handle, options_t.delete_files)


async def close_session(session_handle: session):
    async with open('session.dat', 'wb') as session_state_savefile:
        await session_state_savefile.write(bencode(session_handle.save_state()))
