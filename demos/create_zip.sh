#!/bin/bash -f

for d in canvas-mind sketch-dynamics motion-harness; do zip -r "$d.zip" "$d" -x '*/.*'; done