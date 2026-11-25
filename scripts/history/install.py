#!/usr/bin/env python3
"""Compatibility launcher; optional components are managed by the installed CLI."""
import os
import sys

os.execvp('strathive', ['strathive', 'data', 'history', 'install', *sys.argv[1:]])
