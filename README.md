# Liminal Kid apt repository

Packages for handhelds running sndstation: [Twill Studio](https://github.com/LiminalKid/TwillStudio)
and whatever comes after it.

- Address: `https://liminalkid.github.io/apt`, suite `stable`, component `main`, arm64
- Signing key: [`liminalkid.asc`](liminalkid.asc), ed25519, fingerprint `A77F 0C19 7200 A7D4 D155 EED9 9AC4 3A71 B408 5D4B`
- Vendor definition for sndstation: [`liminalkid.vendor`](liminalkid.vendor)

## Adding it by hand

On the handheld:

    sudo curl -fsSL -o /usr/share/keyrings/liminalkid.asc https://liminalkid.github.io/apt/liminalkid.asc
    printf 'Types: deb\nURIs: https://liminalkid.github.io/apt\nSuites: stable\nComponents: main\nSigned-By: /usr/share/keyrings/liminalkid.asc\n' | sudo tee /etc/apt/sources.list.d/liminalkid.sources
    sudo apt update && sudo apt install twill-studio

## Publishing

    scripts/publish.py path/to/package.deb ...

files each package in `pool/` by its control file's name and version, writes `dists/stable` again and
signs it. Then commit and push; GitHub Pages serves `main`.
