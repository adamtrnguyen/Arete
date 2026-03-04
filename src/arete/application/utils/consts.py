import re

# Image patterns
WIKILINK_IMG_RE = re.compile(r"!\[\[([^\]]+)\]\]")  # ![[path|...]]
MARKDOWN_IMG_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")  # ![](path)

# Non-image wikilinks: [[Target]] or [[Target|display]]
# Negative lookbehind avoids matching image embeds ![[...]]
WIKILINK_RE = re.compile(r"(?<!!)\[\[([^\]]+)\]\]")
