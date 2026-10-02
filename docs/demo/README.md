# Demo recording

`ringsays-demo.mp4`: 65 second walkthrough, 1920x1080, demo data only (Mock Bank is fictional; the
RingSays API runs in its local MOCK environment). Left: bank agent console. Right: customer's own bank
app with the RingSays SDK card. Scenes: Arabic customer picks an offered time, signed webhook books it;
English customer taps Talk now, agent calls, outcome recorded, customer history updates.

Record again after any UI change (about two minutes):

```bash
examples/mock-bank/demo/run.sh            # writes examples/mock-bank/demo/out/ringsays-demo.mp4
```

The script starts the API if needed, builds the SDK, Mock Bank server and app, runs
`examples/mock-bank/app/e2e/demo.spec.ts` (two recorded browser contexts, captions on a timeline,
orange rings where someone taps), then `examples/mock-bank/demo/compose.py` puts both recordings side by
side with the captions using ffmpeg. Change wording in the spec's `say(...)` calls. The presenter
script for live bank meetings is kept as a separate document.
