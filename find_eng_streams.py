import subprocess
import urllib.request
import re

print("Fetching iptv-org english index...")
req = urllib.request.Request("https://iptv-org.github.io/iptv/languages/eng.m3u")
try:
    with urllib.request.urlopen(req) as response:
        content = response.read().decode('utf-8')
except Exception as e:
    print("Failed to fetch index:", e)
    exit(1)

channels = []
current_name = None
current_cat = "General"

for line in content.split('\n'):
    line = line.strip()
    if line.startswith('#EXTINF'):
        m = re.search(r',(.+)$', line)
        if m:
            current_name = m.group(1).replace(' (1080p)', '').replace(' (720p)', '').strip()
            
            group_m = re.search(r'group-title="([^"]+)"', line)
            if group_m:
                group = group_m.group(1)
                current_cat = group
            else:
                if 'Bloomberg' in current_name: current_cat = 'Business'
                elif 'News' in current_name or 'DW' in current_name or 'Al Jazeera' in current_name: current_cat = 'News'
                elif 'NASA' in current_name: current_cat = 'Science'
                elif 'Fashion' in current_name: current_cat = 'Lifestyle'
                elif 'Red Bull' in current_name: current_cat = 'Sports'
                else: current_cat = 'General'
    elif line and not line.startswith('#'):
        if current_name and any(x in current_name.lower() for x in ['news', 'bloomberg', 'nasa', 'red bull', 'cbs', 'abc', 'nbc', 'sky', 'france 24']):
            channels.append((current_name, line, current_cat))
        current_name = None

print(f"Found {len(channels)} potential english channels. Testing some...")

working = []
seen_names = set()

for name, url, cat in channels:
    if len(working) >= 10:
        break
    base_name = name.split(' ')[0]
    if base_name in seen_names or "Geo-blocked" in name:
        continue
        
    cmd = ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-timeout", "5000000", url]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode == 0:
        print(f"OK: {name} | {cat} | {url}")
        working.append((name, url, cat))
        seen_names.add(base_name)

print("\n\nFinal List:")
for w in working:
    print(f'    TVChannel("{w[0]}", "{w[1]}", "{w[2]}"),')
