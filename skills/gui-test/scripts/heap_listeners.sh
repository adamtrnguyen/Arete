#!/usr/bin/env bash
# Read-only snapshot of the live Obsidian renderer after a forced GC:
# JS heap used, DOM element count, and event listeners on window and document.
# Uses the obsidian CLI's CDP passthrough. Prints one line.
set -u
obsidian dev:cdp method=HeapProfiler.collectGarbage > /dev/null
obsidian dev:cdp method=HeapProfiler.collectGarbage > /dev/null
doc_id=$(obsidian dev:cdp method=Runtime.evaluate params='{"expression":"document"}' | sed -n 's/.*"objectId": "\(.*\)".*/\1/p')
win_id=$(obsidian dev:cdp method=Runtime.evaluate params='{"expression":"window"}' | sed -n 's/.*"objectId": "\(.*\)".*/\1/p')
doc_n=$(obsidian dev:cdp method=DOMDebugger.getEventListeners params="{\"objectId\":\"$doc_id\"}" | grep -c '"type"')
win_n=$(obsidian dev:cdp method=DOMDebugger.getEventListeners params="{\"objectId\":\"$win_id\"}" | grep -c '"type"')
stats=$(obsidian dev:cdp method=Runtime.evaluate params='{"expression":"JSON.stringify({heapMB:+(performance.memory.usedJSHeapSize/1048576).toFixed(1), dom:document.getElementsByTagName(\"*\").length, canvases:document.getElementsByTagName(\"canvas\").length})","returnByValue":true}' | sed -n 's/.*"value": "\(.*\)".*/\1/p')
echo "${1:-snapshot} docListeners=$doc_n winListeners=$win_n $stats"
