# StremTui: The Stremio Protocol Meets the Unix Philosophy

Welcome to **StremTui**! This project is a love letter to the terminal-centric Linux world. It embraces the core of the Unix philosophy: write programs that do one thing well, and make them work together. StremTui handles the heavy lifting of searching, resolving, and downloading streams, and then elegantly pipes the output directly to `mpv` for a seamless viewing experience.

What started as a raw scripting challenge to reverse-engineer and understand the Stremio add-on protocol has evolved into a sleek, highly usable Textual User Interface (TUI).

---

## 🎯 Project Objectives & The Vision

The primary goal of this project was to demystify how streaming platforms aggregate decentralized content. Specifically, I wanted to:

1. **Reverse-Engineer the Stremio Protocol:** Understand how Stremio interacts with providers like Cinemeta and Torrentio to aggregate metadata and streams without a centralized database.
2. **Master Complex C++ Bindings in Python:** Integrate `libtorrent` (a powerful, highly complex C++ library) into a modern, asynchronous Python environment.
3. **Build a Sleek Terminal UI:** Prove that terminal applications don't have to be clunky. Using `textual`, I wanted to build an interface that feels as responsive and intuitive as a modern web app, complete with image rendering right in your console.

---

## 🛠️ The Architecture

Behind the minimalist, Unix-abiding terminal interface lies a highly concurrent system designed to bridge entirely different software ecosystems. For the project to work it had to orchestrate a delicate dance between asynchronous Python event loops, heavy C++ BitTorrent engines, and external media players.

* **Asynchronous I/O & UI Fluidity:** To keep the terminal feeling as responsive as a modern web application, the architecture relies heavily on non-blocking concurrency. Network calls to Stremio's Cinemeta and various Torrentio endpoints happen simultaneously using libraries like `httpx` and `curl_cffi`. The UI layer, powered by `textual`, continuously consumes these asynchronous streams, dynamically rendering image bytes and updating nested layouts without ever freezing the user's terminal.
* **Native System Integration:** The core streaming engine requires wrapping `libtorrent`—a deeply complex, high-performance C++ library—within a Python environment. The application safely manages state across these language boundaries. It handles session persistence, parses daily tracker lists to disk, and bootstraps DHT routing, ultimately piping the resulting physical byte stream directly to a standalone `mpv` process.
* **Protocol Navigation:** Operating without a centralized database means navigating a decentralized web of APIs. The system mimics official client behaviors, manages TLS fingerprints to bypass strict provider protections, and parses undocumented JSON structures to aggregate metadata and peer-to-peer swarms seamlessly.

---

## ⚙️ How It Works (Under the Hood)

StremTui operates in four distinct phases, seamlessly flowing from a keystroke to a playing video.

### 1. Catalog & Metadata (Cinemeta)

When you type a search query, StremTui fires asynchronous requests to Stremio's **Cinemeta API**. This returns a list of media objects (movies or series) along with their IMDb IDs, posters, descriptions, and cast lists. The UI immediately reacts, populating horizontal scroll lists with loaded image bytes.

### 2. Stream Resolution (Torrentio)

Once you select a movie or an episode, the application takes the IMDb ID and queries **Torrentio**. Torrentio acts as a scraper, returning a list of available torrent swarms for that specific piece of media, including the crucial `infoHash` and file indices.

### 3. The BitTorrent Engine (Libtorrent)

This is where the magic happens. We don't just download a file; we stream it.
StremTui initializes a `libtorrent` session. To guarantee lightning-fast metadata acquisition and peer discovery, it caches DHT (Distributed Hash Table) routing states between sessions and pulls daily tracker lists from Ngosang's GitHub repository.

Instead of downloading the torrent randomly (the default BitTorrent behavior), StremTui sets `torrent_flags.sequential_download` and prioritizes only the specific video file index. It downloads a 50MB buffer directly to disk.

### 4. Playback (MPV)

Once the 50MB buffer is hit, StremTui pauses its own blocking UI, spawns an `mpv` subprocess pointing to the growing buffer file, and lets you watch the video while `libtorrent` continues seeding and leeching in the background.

---

## 🧗 The Challenges Conquered

Building StremTui was not without its hurdles. Here are the main challenges tackled during development:

* **Taming Libtorrent:** `libtorrent` is incredibly powerful but notoriously complex to configure for *streaming* rather than standard downloading. Forcing it to download pieces sequentially and ignoring unwanted files in a large season pack required deep dives into the library's `torrent_info` and prioritization APIs.
* **Bypassing Provider Blocks:** Standard HTTP libraries often get blocked by stream providers relying on TLS fingerprinting (like Cloudflare). To solve this, I integrated `curl_cffi` to impersonate a standard browser's TLS handshake, ensuring consistent access to the Torrentio API.
* **State Management in TUIs:** Updating a terminal UI with high-resolution images and dynamic lists without blocking the main event loop required careful use of `textual`'s `@work(exclusive=True)` decorators and async generators (`as_completed`).
* **Cold Start Speeds:** Torrents are historically slow to start. By persisting the `session.dat` and `tracker_cache` locally, StremTui remembers the DHT nodes and top trackers from previous runs, cutting down the "Downloading Metadata..." phase from minutes to mere seconds.

---

## 🚀 What's Next: The Roadmap

StremTui is already a highly capable terminal companion, but the Unix philosophy teaches us that software can always be refined and extended. Here is what is cooking in the pipeline to make this the ultimate terminal streaming experience:

### 1. External Subtitle Integration

Currently, StremTui relies entirely on the embedded subtitles within the downloaded video file. But we all know the frustration of booting up a highly anticipated movie only to find the embedded subtitles are out of sync or missing your preferred language!

The next step is to tap into external subtitle providers (like OpenSubtitles via Stremio add-ons). We will scrape the `.srt` or `.vtt` files, download them to our temporary buffer directory, and pipe them directly into our trusty `mpv` player using the `--sub-file` argument. True freedom means watching what you want, how you want it, without leaving the terminal.

### 2. True Dynamic Seeking (The Holy Grail of Torrent Streaming)

Right now, StremTui uses `torrent_flags.sequential_download`. This works beautifully if you watch a movie from start to finish. However, if you try to skip ahead 45 minutes, the player will freeze. Why? Because `mpv` is asking for bytes that `libtorrent` hasn't even thought about downloading yet.

Solving this is a fantastic engineering challenge. We need to build a two-way bridge between the `mpv` playback cursor and the `libtorrent` piece prioritization engine. Here is the technical breakdown of how we will achieve this:

1. **IPC Communication:** We will establish an Inter-Process Communication (IPC) socket with `mpv` to constantly read the current playback time and listen for "seek" events.
2. **Time-to-Byte Translation:** When a user skips to a new timestamp, we must calculate where that time exists in the physical file. Assuming a relatively constant bitrate, we can estimate the target byte position using:

$$Byte\_Target \approx \left( \frac{Target\_Time\_(s)}{Total\_Duration\_(s)} \right) \times Total\_File\_Size\_(bytes)$$


3. **Byte-to-Piece Mapping:** Torrents are divided into chunks called "pieces". To tell `libtorrent` what to download, we map the target byte to its corresponding piece index:

$$Piece\_Index = \lfloor \frac{Byte\_Target}{Piece\_Size\_(bytes)} \rfloor$$


4. **Dynamic Re-Prioritization:** Once we have the target `Piece_Index`, we will dynamically update `libtorrent`'s priorities on the fly. We will set the new target pieces (and the ones immediately following them) to the maximum priority, while deprioritizing the skipped pieces.

This will result in a buffer that "follows" the user's cursor, providing a smooth, responsive, Netflix-like scrubbing experience entirely powered by decentralized peer-to-peer swarms.

---
