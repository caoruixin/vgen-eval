"""Verify the committed PoC snapshot offline with Python's standard library.

No downloads, model loading, environment installation or generation requests.
"""
import hashlib
import json
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def sha256(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def main():
    manifest = json.loads((ROOT / 'PACKAGE-MANIFEST.json').read_text())
    failed = []
    for row in manifest['files']:
        rel = Path(row['path'])
        if rel.is_absolute() or '..' in rel.parts:
            raise ValueError('Unsafe manifest path')
        path = ROOT / rel
        if path.is_symlink() or not path.is_file():
            failed.append({'path': str(rel), 'reason': 'missing or symlink'})
        elif path.stat().st_size != row['packaged_size'] or sha256(path) != row['packaged_sha256']:
            failed.append({'path': str(rel), 'reason': 'size or SHA256 mismatch'})
    videos = {}
    for name, expected in [('self', 77), ('api', 10)]:
        run = ROOT / 'runs' / name
        valid = []
        for path in (run / 'records').glob('*.json'):
            record = json.loads(path.read_text())
            if record.get('status') != 'validated':
                continue
            rel = Path(record['video_path'])
            if rel.is_absolute() or '..' in rel.parts:
                raise ValueError('Unsafe video path')
            video = run / rel
            valid.append(record)
            if not video.is_file() or sha256(video) != record['media']['sha256']:
                failed.append({'path': str(video.relative_to(ROOT)), 'reason': 'original-video SHA256 mismatch'})
        videos[name] = len(valid)
        if len(valid) != expected:
            failed.append({'path': 'runs/' + name, 'reason': 'unexpected validated-video count'})

    class Sources(HTMLParser):
        def __init__(self):
            super().__init__()
            self.videos = []
            self.images = []

        def handle_starttag(self, tag, attrs):
            src = dict(attrs).get('src')
            if tag == 'video' and src:
                self.videos.append(src)
            if tag == 'img' and src:
                self.images.append(src)

    review = ROOT / 'quality' / 'blind-review'
    sources = Sources()
    sources.feed((review / 'review.html').read_text())
    if len(sources.videos) != 20 or not all((review / src).is_file() for src in sources.videos + sources.images):
        failed.append({'path': 'quality/blind-review', 'reason': 'missing review media'})
    result = {'passed': not failed, 'manifest_files': len(manifest['files']),
              'self_original_videos': videos['self'], 'official_original_videos': videos['api'],
              'blind_review_videos': len(sources.videos), 'failed': failed,
              'scope': 'Packaged SHA256 plus original-video SHA256 from measured request records'}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not failed else 1


if __name__ == '__main__':
    sys.exit(main())
