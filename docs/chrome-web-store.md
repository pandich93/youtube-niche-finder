# Publish the extension in the Chrome Web Store

Today the extension is loaded unpacked from a release zip or from `extension/`
(see [extension/README.md](../extension/README.md#installation)). For a hosted
copy ([hosting.md](hosting.md)) a Web Store listing saves every person that
step and keeps them updated. Nothing here is automated: publishing is done by
the owner of the developer account, by hand.

## Checklist

### Once

- [ ] **Developer account.** Register at the
      [Chrome Web Store developer dashboard](https://chrome.google.com/webstore/devconsole)
      with the Google account that will own the listing. It costs a one-time
      registration fee, needs 2-step verification on that account and a
      verified contact email.
- [ ] **Publisher details.** Publisher name, and the trader / non-trader
      declaration (EU). A trader shows an address and phone number in the
      listing.
- [ ] **Privacy policy URL.** `https://YOUR-DOMAIN/privacy` of your hosted copy,
      or [PRIVACY.md](../PRIVACY.md) on GitHub. The section "The Chrome
      extension" there describes exactly what the extension sends and where.
- [ ] **Visibility for a closed beta.** "Unlisted" (anyone with the link) or
      "Private" (only the testers' emails or a Google Group). "Public" can wait
      until the beta ends.

### Each upload

- [ ] **Version.** `extension/manifest.json` `version` must be higher than the
      published one. The project bumps it at release time together with the
      CHANGELOG section ([release workflow](../.github/workflows/release.yml)).
- [ ] **Package.** The store wants `manifest.json` at the root of the zip. The
      release zip has a top folder (`niche-finder-extension/`), so build the
      store zip without it:

      ```sh
      mkdir -p dist
      git archive --format=zip -o dist/niche-finder-extension-store.zip HEAD:extension
      ```

- [ ] **Smoke test the zip** before uploading: unzip it, "Load unpacked" on
      `chrome://extensions`, open a YouTube video, check the panel against a
      local backend and against your domain (token with `read` + `write`).

### The listing (first upload, then when it changes)

- [ ] **Texts.** Name and short description come from `_locales/*/messages.json`
      (`extName`, `extDescription`, at most 132 characters); the detailed
      description is written in the dashboard, in English and Russian.
      The name mentions YouTube: the store's trademark policy accepts "for
      YouTube"-style descriptions, but a reviewer may ask to reword it.
- [ ] **Category.** "Productivity" (or "Tools").
- [ ] **Images.** The 128x128 icon is `extension/icons/icon128.png`. Also
      needed: at least one screenshot (1280x800 or 640x400) and the small
      promo tile (440x280). The README screenshots in `assets/` are a start.

### The privacy tab (what reviewers check most)

- [ ] **Single purpose:** "Shows niche-finder analytics (outliers, views per
      hour, channel analytics) on YouTube pages, from the user's own
      niche-finder service."
- [ ] **Permission justifications:**

      | Permission | Why |
      | --- | --- |
      | `storage` | the popup settings, and the personal API token in `chrome.storage.local` |
      | `alarms` | a periodic health and alerts check for the icon badge |
      | host `http://127.0.0.1/*`, `http://localhost/*` | the default service address: the user's own backend on their machine |
      | optional host `https://*/*`, `http://*/*` | the user's own server, whose address is unknown when the extension is built. Requested at runtime for the one host the user enters; access to the previous host is given back when the address changes |
      | content script on `https://www.youtube.com/*` | draws the panels and badges on YouTube pages |

      A store build for a single service can narrow the optional hosts to
      `https://YOUR-DOMAIN/*` in its `manifest.json`: a narrower permission
      usually reviews faster, at the cost of a build that only works with that
      domain (and localhost).
- [ ] **Remote code:** "No". All the JavaScript is in the package; nothing is
      loaded or evaluated from the network.
- [ ] **Data usage.** Declare "Website content" (video and channel IDs on the
      YouTube page you are on, text typed into the popup) and "Authentication
      information" (the API token). Certify that the data is not sold, not
      used for anything unrelated to the single purpose, and not used for
      creditworthiness or lending.

### After the first upload

- [ ] **The item ID.** The store assigns a fixed ID (the 32 letters in the
      listing's address). Put it into the server's `.env`:
      `NF_EXTENSION_IDS=<store id>` (comma-separated, add the ID of an unpacked
      build you debug with), then `make prod-up`. See
      [hosting.md](hosting.md#the-chrome-extension).
- [ ] **Review.** It takes from a day to a few weeks; every update is reviewed
      again. Broad host permissions are the usual reason for a longer review.
- [ ] **Tell the testers.** The listing link, and the three steps from
      [hosting.md](hosting.md#the-chrome-extension): token with `read` +
      `write`, address `https://YOUR-DOMAIN`, allow access. Chrome updates the
      extension by itself after each published version.
