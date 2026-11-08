"""Local character memory. Missing facts remain unknown; no image processing."""
import json
import threading
import unicodedata
import uuid
from pathlib import Path

from result_store import output_folder

_lock = threading.Lock()


def key(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Character names must be non-empty strings")
    return unicodedata.normalize("NFKC", value).strip().casefold()


class CharacterStore:
    def __init__(self, root, output_dir):
        self.root = Path(root)
        self.output_dir = output_dir

    def get(self, name):
        query = key(name)
        matches = []
        for path in self.root.glob("*.json"):
            profile = json.loads(path.read_text(encoding="utf-8"))
            names = [profile["id"], profile["folder_name"], profile["name_ko"], profile["name_en"], *profile["aliases"]]
            if query in {key(n) for n in names if n}:
                matches.append(profile)
        if len(matches) > 1:
            raise ValueError("Ambiguous character name; use a unique full name")
        return matches[0] if matches else None

    def save(self, name, **fields):
        # Partial updates preserve unknown/previous facts; empty lists explicitly clear lists.
        key(name)
        allowed = {"name_ko", "name_en", "aliases", "appearance", "age", "era", "default_style", "reference_images"}
        if fields.keys() - allowed:
            raise ValueError("Unknown character fields")
        fields = {k: v for k, v in fields.items() if v is not None}
        for field, value in fields.items():
            if field in {"aliases", "reference_images"}:
                if not isinstance(value, list) or any(not isinstance(v, str) or not v.strip() for v in value):
                    raise ValueError(f"{field} must be a list of non-empty strings")
            elif not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field} must be a non-empty string")
        with _lock:
            identifiers = [name, fields.get("name_ko"), fields.get("name_en"), *fields.get("aliases", [])]
            matches = {p["id"]: p for candidate in identifiers if candidate
                       for p in [self.get(candidate)] if p}
            if len(matches) > 1:
                raise ValueError("Name or alias belongs to another character")
            existing = next(iter(matches.values()), None)
            profile = dict(existing) if existing else dict(
                id=uuid.uuid4().hex, name_ko=None, name_en=None, aliases=[name.strip()],
                appearance=None, age=None, era=None, default_style=None, reference_images=[])
            profile.update(fields)
            names = [profile["name_ko"], profile["name_en"], *profile["aliases"]]
            if key(name) not in {key(n) for n in names if n}:
                profile["aliases"] = [*profile["aliases"], name.strip()]
            for candidate in [profile["name_ko"], profile["name_en"], *profile["aliases"]]:
                other = self.get(candidate) if candidate else None
                if other and other["id"] != profile["id"]:
                    raise ValueError("Name or alias belongs to another character")
            folder_name = existing["folder_name"] if existing else profile["id"]
            folder = output_folder(self.output_dir, folder_name)
            folder.mkdir(parents=True, exist_ok=True)
            profile.update(folder_name=folder_name, image_dir=str(folder))
            self.root.mkdir(parents=True, exist_ok=True)
            target = self.root / (profile["id"] + ".json")
            temporary = target.with_suffix(".tmp")
            try:
                temporary.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
            return profile
