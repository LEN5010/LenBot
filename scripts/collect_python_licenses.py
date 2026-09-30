"""Collect actual installed wheel metadata and declared notice files for a distribution."""

import argparse
from importlib.metadata import distributions
import json
from pathlib import Path
import re
import shutil


def collect(output: Path) -> None:
    output.mkdir(parents=True)
    records = []
    for distribution in sorted(distributions(), key=lambda item: item.metadata['Name'].casefold()):
        name, version = distribution.metadata['Name'], distribution.version
        destination = output / (re.sub(r'[^a-zA-Z0-9_.-]', '-', name) + '-' + version)
        destination.mkdir()
        files = distribution.files
        notices = []
        if files is not None:
            for file in files:
                if not re.match(r'^(licen[cs]es?|copying|notice|copyright)(?:[._-].*|$)', file.name, re.I):
                    continue
                source = Path(distribution.locate_file(file))
                if not source.is_file():
                    raise FileNotFoundError(f'Installed notice file is missing: {source}')
                # Preserve distinct notices with the actual wheel-relative path, not a synthetic id.
                if '..' in file.parts or file.is_absolute():
                    raise ValueError(f'Installed notice points outside its distribution: {file}')
                target = destination / file
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                notices.append(str(file))
        metadata = {'name': name, 'version': version,
                    'license_expression': distribution.metadata.get('License-Expression'),
                    'license_text_metadata': distribution.metadata.get('License'),
                    'license_classifiers': [value for value in distribution.metadata.get_all('Classifier', [])
                                            if value.startswith('License ::')],
                    'file_manifest_available': files is not None, 'notice_files': notices}
        (destination / 'metadata.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2))
        records.append(metadata)
    (output / 'index.json').write_text(json.dumps({
        'source': 'Actual installed Python distributions in this locked build', 'packages': records,
        'missing_license_metadata': [item['name'] for item in records
                                    if item['license_expression'] is None and item['license_text_metadata'] is None
                                    and not item['license_classifiers']],
        'missing_notice_files': [item['name'] for item in records if not item['notice_files']],
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    collect(parser.parse_args().output)
