# CrossMuse 🎧

Experience your music in a fresh way with **CrossMuse**, an open-source application that lets you seamlessly play crossfading clips from your favorite YouTube Music playlists. Discover new music and enjoy dynamic transitions, creating your own personalized DJ mix. **For more detailed information and usage instructions, be sure to check out the project Wiki.**


🚀 **Key Features**

✅ Effortlessly search for, save, and load YouTube Music playlists directly within the app.  
✅ Downloads and locally caches songs using [yt-dlp](https://github.com/yt-dlp/yt-dlp).  
✅ Dynamically generates crossfaded clips from songs.  
✅ Configurable clip length (including full song playback), crossfade duration, and prefill time.  
✅ Includes shuffle and repeat playback options with intelligent shuffle logic.  
✅ User-friendly Tkinter GUI with intuitive controls for playback and configuration.

---

## 🖥️ Quickstart

A prebuilt **Windows executable** is available for download on the [Releases](https://github.com/StinsonEvolutions/CrossMuse/releases) page. Simply:

1. **Download** `CrossMuse.exe` from the latest release.
2. **Run** `CrossMuse.exe` (no installation required).

Note: The `.exe` version is self-contained but may trigger security warnings (since it's unsigned). If you prefer, you can run CrossMuse directly from source (see below). A background console window will appear when running the `.exe`; this can be minimized but must remain open.

---

## 📥 Installation & Running Locally

1️. **Clone the Repository**
```

git clone [https://github.com/StinsonEvolutions/CrossMuse.git](https://www.google.com/search?q=https://github.com/StinsonEvolutions/CrossMuse.git)
cd CrossMuse

```
2️. **Install Dependencies**
```

pip install -r requirements.txt

```
3️. **Run the Application**
```

python cm\_main.py

```

A console window will appear behind the main application window; this can be minimized if desired, but must remain open while the application is running.

---

## 📦 Packaging as a Standalone App

For Windows users who prefer a single `.exe`, you can package CrossMuse with:
```

pyinstaller --onefile --collect-all ytmusicapi --add-data "Sample Playlists\*;Sample Playlists" --add-data "logo.png;." --name "CrossMuse" cm\_main.py

```
This creates a **self-contained executable** with no extra setup required. Note that the `--noconsole` option is intentionally omitted to provide a background console window for ffmpeg output.

---

## 💡 Why CrossMuse?

Tired of static playlists? CrossMuse offers a unique and customizable DJ-style music experience. While some services provide basic crossfade, CrossMuse lets you:

- **Effortlessly discover and manage playlists:** Search, save, and load YouTube Music playlists directly within the app.
- **Create dynamic mixes:** Enjoy seamless crossfades between songs or configurable clips.
- **Customize your listening:** Set clip lengths (from short snippets to full songs), crossfade durations, and enable shuffle or repeat.
- **Experiment with playback:** Create lively mixes with short clips or smooth ambient flows with long crossfades.

---

## ⚠️ Legal Disclaimer

CrossMuse and its contributors **are not responsible** for any copyright infringement arising from downloading songs. **Use responsibly and comply with all applicable laws.**

---

## 🤝 Contributing & Feedback

We'd love your help in making CrossMuse even better!
✅ **Report bugs & suggest features** via GitHub Issues
✅ **Contribute code** by forking & submitting a pull request
✅ **Stay updated** by checking out our [Wiki](https://github.com/StinsonEvolutions/CrossMuse/wiki)

🎧 **Enjoy your new and dynamic way of experiencing music!**
