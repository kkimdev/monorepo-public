#!/usr/bin/make -f

# Disable implicit rules so canonical targets will work.
.SUFFIXES:
# Remove some rules from gmake that .SUFFIXES does not remove.
SUFFIXES=

.DEFAULT_GOAL := help

.PHONY: build
build: clean
	mkdir -p ./dist/

	cp manifest.json ./dist/
	cp options_ui.html ./dist/
	tsc

# https://react-icons.github.io/react-icons/search?q=window
	curl -L https://raw.githubusercontent.com/microsoft/vscode-icons/main/icons/light/multiple-windows.svg -o ./dist/icon.svg
# apt-get install graphicsmagick-imagemagick-compat -y
	convert -size 128x128 ./dist/icon.svg ./dist/icon-128.png
	rm ./dist/icon.svg

	cd ./dist/ && zip -r dist.zip ./

.PHONY: build
clean:
	rm -rf ./dist/
