import yaml  # type: ignore

# ---------- YAML dumper that preserves multiline strings & math ----------


class _LiteralDumper(yaml.SafeDumper):
    def ignore_aliases(self, data):
        return True


def _str_representer(dumper, data: str):
    # Triggers for |- block scalar: matching Obsidian plugin.
    # We exclude characters common in keys like '_' and '^' to avoid unnecessary quoting.
    # Newlines always trigger |- block style.
    triggers = ("\n", "\t", "$", "\\", "{", "}", "~", "'", '"', ":", "[", "]", "#")
    if "\n" in data or any(ch in data for ch in triggers):
        # The key to |- is removing the trailing newline if it exists,
        # and then using style='|'. PyYAML will add |- if it ends without a newline.
        if data.endswith("\n"):
            data = data.rstrip("\n")
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


_LiteralDumper.add_representer(str, _str_representer)
