import json
import re

BROWSER_FILE = "./auth/browser.json"

fetch = input("Enter fetch (node.js): ")

def get_header(name):
    pattern = rf'"{re.escape(name)}"\s*:\s*"((?:\\.|[^"\\])*)"'
    match = re.search(pattern, fetch, re.DOTALL)

    if not match:
        raise ValueError(f"Need {name} value")

    return json.loads(f'"{match.group(1)}"')

browser = {
    "accept": "*/*", 
    "accept-encoding": "gzip, deflate", 
    "content-encoding": "gzip", 
    "content-type": "application/json", 
    "origin": "https://music.youtube.com",     
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:88.0) Gecko/20100101 Firefox/88.0",     
    "x-origin": "https://music.youtube.com"
}

browser["authorization"] = get_header("authorization")
browser["cookie"] = get_header("cookie")
browser["x-goog-authuser"] = get_header("x-goog-authuser")
browser["x-goog-visitor-id"] = get_header("x-goog-visitor-id")

with open(BROWSER_FILE, "w") as file:
    json.dump(browser, file, indent=4, sort_keys=True)