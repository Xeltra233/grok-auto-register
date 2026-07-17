# Vendored GoProxy

Source: https://github.com/isboyjc/GoProxy
Purpose: merged into this project so the register stack can use local HTTP/SOCKS ports
without deploying a separate proxy service (e.g. Zeabur constraints).

Layout:
- `third_party/goproxy/` — upstream GoProxy source (HTTP 7776/7777, SOCKS5 7779/7780, WebUI 7778)
- `third_party/goproxy/bin/` — optional local build/prebuilt binaries (gitignored)
- `data/goproxy/` — runtime data dir for the embedded instance

Do not run this as a separate deployed app by default; the branch panel will manage it
as a local subprocess and bind register/CPA traffic to 127.0.0.1 ports.
