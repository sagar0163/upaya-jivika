# Changelog

## [0.5.0](https://github.com/sagar0163/upaya-jivika/compare/v0.4.0...v0.5.0) (2026-09-05)


### Features

* **approval-gate:** veto-window human oversight for large AI spends (§14) ([804236b](https://github.com/sagar0163/upaya-jivika/commit/804236b27993cceb4d4f99bd6c8c9f1478542b7d))
* **captcha:** add bot-detection strategy — vendor probing, escalation ladder, playwright-stealth (§19) ([b537277](https://github.com/sagar0163/upaya-jivika/commit/b53727739401aff89e5be150e632140d80c9e5fd))
* **captcha:** implement real nodriver/Camoufox/2Captcha bypass ladder ([15373f2](https://github.com/sagar0163/upaya-jivika/commit/15373f2a5e0882e46dafbad6425cd006de8dd9f2))
* **email:** add email inbox integration — verification links/codes + payment alerts (critical gap) ([e186d7e](https://github.com/sagar0163/upaya-jivika/commit/e186d7efa5535918513d444ccf9723edf79d1b64))
* **payments:** add Payoneer webhook for payment confirmation (§20) ([a2b15ab](https://github.com/sagar0163/upaya-jivika/commit/a2b15abdcc6711a01c6a7b4dc43582cbe7df1c4a))
* **scam-detection:** wire payment-window tracking into the autonomous loop ([f766690](https://github.com/sagar0163/upaya-jivika/commit/f76669045f342d3ca5219c6b526d62e529eb9070))
* **scam:** add scam-handling core — legitimacy scoring, payment windows, blacklist, chargeback reversal (§20) ([ffdd9eb](https://github.com/sagar0163/upaya-jivika/commit/ffdd9ebb506ee860146b3323c4fa40e0619cf80e))
* **task-execution:** wire TaskExecutor into the autonomous loop ([03e4b47](https://github.com/sagar0163/upaya-jivika/commit/03e4b47ccfee3911273e7c1071a80aeeeeb2a0d3))
* **withdrawal:** add withdrawal mechanism — dashboard UI + Payoneer payout (critical gap) ([439c955](https://github.com/sagar0163/upaya-jivika/commit/439c95595e75261ca86a0c6481fda35276f656a3))


### Bug Fixes

* **approval-gate:** guard survival_tick against persistence failures ([5453443](https://github.com/sagar0163/upaya-jivika/commit/5453443980616e3995d0a13a89b6601a90a48095))
* **captcha:** run blocking sync_playwright() lookup off the event loop ([1b616dd](https://github.com/sagar0163/upaya-jivika/commit/1b616dd4e0b40336f5b2d3922bacb8b3e930fe7b))
* close browser context leak on failed login, atomic payment claim ([e26d2ba](https://github.com/sagar0163/upaya-jivika/commit/e26d2ba8fb22d1c08db6cb2fc4bbacb5098817b6))
* declare missing env vars in render.yaml ([8ff7fc7](https://github.com/sagar0163/upaya-jivika/commit/8ff7fc7984bd805802d52de19a724ca44440396a))
* **deploy:** install playwright chromium binary during build ([5e832e3](https://github.com/sagar0163/upaya-jivika/commit/5e832e362ffa4c76df20986c6bdff7a4572d3ae9))
* restore CARRY_FORWARD knowledge on rebirth, fix cold-archive corruption ([344b9f5](https://github.com/sagar0163/upaya-jivika/commit/344b9f5cbedbe94d6f025bd643ffd4b2920ceba5))
* **security:** require API_AUTH_TOKEN on every mutating endpoint ([08913a4](https://github.com/sagar0163/upaya-jivika/commit/08913a4ae39414f0d9cdbe09fc3043d140b36e48))
* **vault:** encrypt platform passwords at rest ([4c16263](https://github.com/sagar0163/upaya-jivika/commit/4c16263d2e473da159c230d70501ebab88e098c1))

## [0.4.0](https://github.com/sagar0163/upaya-jivika/compare/v0.3.0...v0.4.0) (2026-09-04)


### Features

* add alert system notifying at Critical/Terminal survival states ([#42](https://github.com/sagar0163/upaya-jivika/issues/42)) ([8c66cd9](https://github.com/sagar0163/upaya-jivika/commit/8c66cd931b5803805e43e54bd276b1928476b621))
* add ancestral memory loader to compress past Soul Crystals on rebirth ([#24](https://github.com/sagar0163/upaya-jivika/issues/24)) ([c1cbdf8](https://github.com/sagar0163/upaya-jivika/commit/c1cbdf82ae34670626b243656a685ebee789abc3)), closes [#22](https://github.com/sagar0163/upaya-jivika/issues/22)
* add audit trail logging every scored/executed decision ([#40](https://github.com/sagar0163/upaya-jivika/issues/40)) ([cff6624](https://github.com/sagar0163/upaya-jivika/commit/cff6624016f02fb3a9695e4ea9976da36906b0fe))
* add credentials vault and per-provider rate-limit tracker ([#31](https://github.com/sagar0163/upaya-jivika/issues/31)) ([8b56b3d](https://github.com/sagar0163/upaya-jivika/commit/8b56b3dbe368f9b3b69ed248186fed92cf797bad))
* add ethical guardrail hard-blacklist to task pipeline ([#34](https://github.com/sagar0163/upaya-jivika/issues/34)) ([5d4b931](https://github.com/sagar0163/upaya-jivika/commit/5d4b9319c106447150c9bd952b34105fccadb72e)), closes [#33](https://github.com/sagar0163/upaya-jivika/issues/33)
* add HuggingFace cold-archive layer (Layer 3) to diary writer ([#32](https://github.com/sagar0163/upaya-jivika/issues/32)) ([1011680](https://github.com/sagar0163/upaya-jivika/commit/1011680263f3f2692fb3229df55f6b88221a3e44))
* add render.yaml for infrastructure-as-code deployment ([#29](https://github.com/sagar0163/upaya-jivika/issues/29)) ([3f409c1](https://github.com/sagar0163/upaya-jivika/commit/3f409c112d7e002be3276af25f6b2a8d905768c1)), closes [#25](https://github.com/sagar0163/upaya-jivika/issues/25)
* add respawn policy for fresh-slate vs carry-forward task scores ([#44](https://github.com/sagar0163/upaya-jivika/issues/44)) ([f4cc7d1](https://github.com/sagar0163/upaya-jivika/commit/f4cc7d1f24ebfb60fa507444b48d3f0880602ce5)), closes [#43](https://github.com/sagar0163/upaya-jivika/issues/43)
* add task timeout cap while debt keeps ticking ([#38](https://github.com/sagar0163/upaya-jivika/issues/38)) ([6516dc3](https://github.com/sagar0163/upaya-jivika/commit/6516dc30cb3ab8a89b1dd26203d27b076aa017c5))
* complete Playwright platform connectors with session persistence ([#30](https://github.com/sagar0163/upaya-jivika/issues/30)) ([c42e0f0](https://github.com/sagar0163/upaya-jivika/commit/c42e0f0d8cad8271359a355af4c9bf78b09ced2d)), closes [#26](https://github.com/sagar0163/upaya-jivika/issues/26)
* migrate debt_tick/research_trigger to GitHub Actions cron ([#28](https://github.com/sagar0163/upaya-jivika/issues/28)) ([b9074ca](https://github.com/sagar0163/upaya-jivika/commit/b9074cab4d55ba13ff57625dd2183a2bf7eb34a9)), closes [#21](https://github.com/sagar0163/upaya-jivika/issues/21)


### Bug Fixes

* **ci:** run ruff + pytest instead of Node no-op ([#36](https://github.com/sagar0163/upaya-jivika/issues/36)) ([9a53057](https://github.com/sagar0163/upaya-jivika/commit/9a53057ae28ee3102f1bab20e9729cf08a9113d1))
* **persistence:** make Supabase save_events replace instead of append ([#46](https://github.com/sagar0163/upaya-jivika/issues/46)) ([0aae8f1](https://github.com/sagar0163/upaya-jivika/commit/0aae8f106ce459f21399ccfcedb1d853a687958d)), closes [#45](https://github.com/sagar0163/upaya-jivika/issues/45)
* **persistence:** preserve soul-crystal archive across reincarnation wipe ([#51](https://github.com/sagar0163/upaya-jivika/issues/51)) ([dcc5fa9](https://github.com/sagar0163/upaya-jivika/commit/dcc5fa9d1f3b9f0f81ca186f13ba2a15b9109700))
* **survival:** never resume a torn partial-write snapshot ([#53](https://github.com/sagar0163/upaya-jivika/issues/53)) ([73e9be4](https://github.com/sagar0163/upaya-jivika/commit/73e9be46afb6987c8f9825fcbd896ec87cc850c4)), closes [#52](https://github.com/sagar0163/upaya-jivika/issues/52)
* **tests:** make test_full_cycle_mock deterministic ([#49](https://github.com/sagar0163/upaya-jivika/issues/49)) ([5f9596f](https://github.com/sagar0163/upaya-jivika/commit/5f9596f065333837ca9dcf5aa600e4d65c810471))
* **tests:** make websocket debt_tick broadcast test deterministic ([#50](https://github.com/sagar0163/upaya-jivika/issues/50)) ([1c2b238](https://github.com/sagar0163/upaya-jivika/commit/1c2b2383f6a1f542debbaebca37d6674520ac0f8))

## [0.3.0](https://github.com/sagar0163/upaya-jivika/compare/v0.2.0...v0.3.0) (2026-09-03)


### Features

* add /status endpoint ([#16](https://github.com/sagar0163/upaya-jivika/issues/16)) ([f737ded](https://github.com/sagar0163/upaya-jivika/commit/f737ded693989db40d1b686a6bb88a1b05cdcc6d))
* add WebSocket broadcast for live survival state ([#19](https://github.com/sagar0163/upaya-jivika/issues/19)) ([5c71fd7](https://github.com/sagar0163/upaya-jivika/commit/5c71fd706ca41c457450cf8de2a15686f777d8c8))
* implement GitHub diary writer (PyGithub) ([#17](https://github.com/sagar0163/upaya-jivika/issues/17)) ([fac09d3](https://github.com/sagar0163/upaya-jivika/commit/fac09d3bc4e8f9cffc4f2741be93c1e10bf9f981)), closes [#12](https://github.com/sagar0163/upaya-jivika/issues/12)
* implement task_scorer.py + task_executor.py ([#18](https://github.com/sagar0163/upaya-jivika/issues/18)) ([33002ca](https://github.com/sagar0163/upaya-jivika/commit/33002ca73b98644d22fc6abfaa73b70bef003331))
* serve live survival dashboard UI ([#20](https://github.com/sagar0163/upaya-jivika/issues/20)) ([d1a7163](https://github.com/sagar0163/upaya-jivika/commit/d1a716363bb06be96cecbd17c78f0925c0ed7c06))
* wire survival loop + Supabase persistence ([#10](https://github.com/sagar0163/upaya-jivika/issues/10)) ([bcdf0e4](https://github.com/sagar0163/upaya-jivika/commit/bcdf0e48a60d51a2d61d2672f93c5d361cf50618))

## [0.2.0](https://github.com/sagar0163/upaya-jivika/compare/v0.1.0...v0.2.0) (2026-09-03)


### Features

* add minimal FastAPI entrypoint for deployment ([9ba3a4e](https://github.com/sagar0163/upaya-jivika/commit/9ba3a4e0e72e6aab7b27d2f1df6be77d523405aa))
* implement brain_router.py + research_loop.py ([#6](https://github.com/sagar0163/upaya-jivika/issues/6)) ([9587516](https://github.com/sagar0163/upaya-jivika/commit/958751615d1b0ffd35ea96855ea31c10f5cd2401))
