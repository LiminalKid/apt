#!/usr/bin/env python3
"""Adds .deb files to the repository and writes its signed index.

    scripts/publish.py [PACKAGE.deb ...]

Each package is filed under pool/main/<letter>/<name>/ by the name, version and architecture its
control file gives (whatever the file was called). Then dists/stable is written again from every
package in the pool: main/binary-<arch>/Packages and Packages.gz, Release, and Release signed two
ways (InRelease, Release.gpg) with the repository's key, from the GnuPG folder in
LIMINALKID_APT_GNUPGHOME (default ~/.liminalkid-apt/gnupg). Commit and push afterwards.
"""

import email.utils
import gzip
import hashlib
import io
import lzma
import os
import shutil
import subprocess
import sys
import tarfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUITE = "stable"
COMPONENT = "main"
GNUPGHOME = os.environ.get("LIMINALKID_APT_GNUPGHOME", os.path.expanduser("~/.liminalkid-apt/gnupg"))
KEY_FILE = os.path.join(ROOT, "liminalkid.asc")


def ar_members(data):
    if data[:8] != b"!<arch>\n":
        raise ValueError("not a .deb (no ar header)")
    at = 8
    while at + 60 <= len(data):
        name = data[at:at + 16].decode().strip().rstrip("/")
        size = int(data[at + 48:at + 58].decode().strip())
        yield name, data[at + 60:at + 60 + size]
        at += 60 + size + (size & 1)


def control_of(deb):
    """The control file's fields, in order, as (name, value) with continuation lines kept."""
    for name, body in ar_members(deb):
        if not name.startswith("control.tar"):
            continue
        if name.endswith(".gz"):
            body = gzip.decompress(body)
        elif name.endswith(".xz"):
            body = lzma.decompress(body)
        elif name.endswith(".zst"):
            raise ValueError("zstd-compressed control archives aren't read; build with gzip or xz")
        with tarfile.open(fileobj=io.BytesIO(body)) as t:
            for m in t.getmembers():
                if m.name.lstrip("./") == "control":
                    text = t.extractfile(m).read().decode()
                    break
            else:
                raise ValueError("no control file in the package")
        fields = []
        for line in text.splitlines():
            if not line.strip():
                continue
            if line[0] in " \t":
                fields[-1] = (fields[-1][0], fields[-1][1] + "\n" + line)
            else:
                k, v = line.split(":", 1)
                fields.append((k.strip(), v.strip()))
        return fields
    raise ValueError("no control archive in the package")


def pool_path(fields):
    f = dict(fields)
    name, version, arch = f["Package"], f["Version"], f["Architecture"]
    first = name[:4] if name.startswith("lib") else name[0]
    return f"pool/{COMPONENT}/{first}/{name}/{name}_{version.split(':')[-1]}_{arch}.deb"


def add(src):
    with open(src, "rb") as fh:
        deb = fh.read()
    rel = pool_path(control_of(deb))
    dst = os.path.join(ROOT, rel)
    if os.path.exists(dst):
        with open(dst, "rb") as fh:
            if fh.read() != deb:
                sys.exit(f"{rel} is already in the pool with other contents; give the package a new version")
        print(f"{rel}: already there")
        return
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copyfile(src, dst)
    print(f"{rel}: added")


def write_index():
    by_arch = {}
    pool = os.path.join(ROOT, "pool")
    for folder, _, names in os.walk(pool):
        for n in sorted(names):
            if not n.endswith(".deb"):
                continue
            path = os.path.join(folder, n)
            with open(path, "rb") as fh:
                deb = fh.read()
            fields = control_of(deb)
            rel = os.path.relpath(path, ROOT)
            if rel != pool_path(fields):
                sys.exit(f"{rel} doesn't match its control file ({pool_path(fields)})")
            lines = [f"{k}: {v}" for k, v in fields]
            lines += [f"Filename: {rel}", f"Size: {len(deb)}",
                      f"MD5sum: {hashlib.md5(deb).hexdigest()}",
                      f"SHA1: {hashlib.sha1(deb).hexdigest()}",
                      f"SHA256: {hashlib.sha256(deb).hexdigest()}"]
            arch = dict(fields)["Architecture"]
            by_arch.setdefault(arch, []).append((dict(fields)["Package"], dict(fields)["Version"], "\n".join(lines)))
    if not by_arch:
        sys.exit("the pool is empty")

    dist = os.path.join(ROOT, "dists", SUITE)
    shutil.rmtree(dist, ignore_errors=True)
    indexes = {}
    for arch, entries in sorted(by_arch.items()):
        entries.sort()
        text = ("\n\n".join(e[2] for e in entries) + "\n").encode()
        base = f"{COMPONENT}/binary-{arch}/Packages"
        indexes[base] = text
        indexes[base + ".gz"] = gzip.compress(text, mtime=0)
    for rel, data in indexes.items():
        path = os.path.join(dist, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)

    release = [
        "Origin: Liminal Kid",
        "Label: Liminal Kid",
        f"Suite: {SUITE}",
        f"Codename: {SUITE}",
        f"Date: {email.utils.formatdate(usegmt=True)}",
        f"Architectures: {' '.join(sorted(by_arch))}",
        f"Components: {COMPONENT}",
        "Description: Liminal Kid's apps for sndstation handhelds",
    ]
    for title, algo in (("MD5Sum", hashlib.md5), ("SHA1", hashlib.sha1), ("SHA256", hashlib.sha256),
                        ("SHA512", hashlib.sha512)):
        release.append(f"{title}:")
        for rel, data in sorted(indexes.items()):
            release.append(f" {algo(data).hexdigest()} {len(data):>8} {rel}")
    release_path = os.path.join(dist, "Release")
    with open(release_path, "w") as fh:
        fh.write("\n".join(release) + "\n")

    key = fingerprint()
    gpg = ["gpg", "--batch", "--yes", "--quiet", "--homedir", GNUPGHOME, "--local-user", key, "--digest-algo", "SHA512"]
    subprocess.run(gpg + ["--clearsign", "-o", os.path.join(dist, "InRelease"), release_path], check=True)
    subprocess.run(gpg + ["--armor", "--detach-sign", "-o", os.path.join(dist, "Release.gpg"), release_path], check=True)
    for arch, entries in sorted(by_arch.items()):
        for name, version, _ in entries:
            print(f"listed {name} {version} ({arch})")


def fingerprint():
    """The key in liminalkid.asc, which must be the one the GnuPG folder signs with."""
    out = subprocess.run(["gpg", "--batch", "--with-colons", "--show-keys", KEY_FILE], check=True,
                         capture_output=True, text=True).stdout
    fpr = next(line.split(":")[9] for line in out.splitlines() if line.startswith("fpr:"))
    have = subprocess.run(["gpg", "--batch", "--homedir", GNUPGHOME, "--with-colons", "--list-secret-keys", fpr],
                          capture_output=True, text=True)
    if have.returncode != 0:
        sys.exit(f"no secret key for {fpr} in {GNUPGHOME}")
    return fpr


def main():
    for src in sys.argv[1:]:
        add(src)
    write_index()


if __name__ == "__main__":
    main()
