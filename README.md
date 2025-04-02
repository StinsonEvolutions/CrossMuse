# CrossMuse 🎵  

**CrossMuse** is an open-source Python application that lets you experience your playlists in a dynamic new way—by playing seamless, crossfading song clips, much like a DJ mix. It downloads songs from a playlist JSON file (using [yt_dlp](https://github.com/yt-dlp/yt-dlp)), dynamically processes random clips, and begins playback once enough audio is pre-buffered.  

🚀 **Current Features (Beta 0.1.0)**  
✅ Loads playlists from a JSON file  
✅ Downloads & caches songs locally  
✅ Dynamically generates crossfaded clips  
✅ Configurable clip length, prebuffer time, and crossfade duration  
✅ Simple Tkinter GUI for playback controls  

---

## 🖥️ Quickstart

A prebuilt **Windows executable** is available for download on the [Releases](https://github.com/yourusername/CrossMuse/releases) page. Simply:  

1. **Download** `CrossMuse.exe` from the latest release.
2. **Run** `CrossMuse.exe` (no installation required).  

Note: The `.exe` version is self-contained but may trigger security warnings (since it's unsigned). If you prefer, you can run CrossMuse directly from source (see below).  

---

## 📥 Installation & Running Locally

1️. **Clone the Repository**  
```
git clone https://github.com/yourusername/CrossMuse.git
cd CrossMuse
```
2️. **Install Dependencies**  
```
pip install -r requirements.txt
```
3️. **Run the Application**  
```
python cm_main.py
```

---

## 📦 Packaging as a Standalone App  

For Windows users who prefer a single `.exe`, you can package CrossMuse with:  
```
pyinstaller --onefile --noconsole --add-data "path\\to\\Sample Playlists\\*;Sample Playlists" --add-data "path\\to\\logo.png;." --name "CrossMuse" cm_main.py
```
This creates a **self-contained executable** with no extra setup required.

---

## 💡 Why CrossMuse?  

Streaming services like Spotify offer basic crossfade, but sometimes you want a **quick, DJ-style mix of your playlist**. CrossMuse provides a **free, open-source way** to blend tracks smoothly without limitations.

---

## ⚠️ Legal Disclaimer  

CrossMuse and its contributors **are not responsible** for any copyright infringement arising from downloading songs. **Use responsibly and comply with all applicable laws.**  

---

## 🤝 Contributing & Feedback  

We’d love your help in making CrossMuse better!  
✅ **Report bugs & suggest features** via GitHub Issues  
✅ **Contribute code** by forking & submitting a pull request  
✅ **Stay updated** by checking out our [Wiki](https://github.com/yourusername/CrossMuse/wiki)  

🎧 **Enjoy your new way of experiencing music!**
