#! /usr/bin/env bash

VER=3.10

if test -z $VIRTUAL_ENV; then
	test -d venv || python$VER -m venv venv
	source venv/bin/activate
	export VIRTUAL_ENV
fi
apt install ffmpeg
pip install -r requirements.txt
python$VER app.py

