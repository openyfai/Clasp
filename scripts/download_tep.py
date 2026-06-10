# Copyright (c) 2026 openyfai (YF)
# Licensed under the Business Source License 1.1 (BSL 1.1)
# See LICENSE file in the project root for full license terms.

import os
import urllib.request
from pathlib import Path

# Common github mirror for the standard TEP dataset files (Downs & Vogel)
# d00.dat is normal operations
# d05.dat is Fault 5
BASE_URL = "https://raw.githubusercontent.com/camaramm/tennessee-eastman-profBraatz/master/"
FILES_TO_DOWNLOAD = [
    "d00.dat",
    "d05.dat"
]

def main():
    data_dir = Path("data/tep")
    data_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"Downloading TEP datasets to {data_dir.absolute()} ...")
    
    for filename in FILES_TO_DOWNLOAD:
        file_path = data_dir / filename
        if file_path.exists():
            print(f"  [SKIPPED] {filename} already exists.")
            continue
            
        url = BASE_URL + filename
        print(f"  [FETCHING] {url} ...")
        try:
            urllib.request.urlretrieve(url, file_path)
            print(f"  [SUCCESS] Downloaded {filename}")
        except Exception as e:
            print(f"  [ERROR] Failed to download {filename}: {e}")
            print(f"          Please download manually from Harvard Dataverse or another mirror.")

if __name__ == "__main__":
    main()
