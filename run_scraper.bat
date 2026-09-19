@echo off
cd /d "%~dp0"
python scraper.py >> output\run_log.txt 2>&1
