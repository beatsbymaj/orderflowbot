# 18. Where FlowForge runs while the PC sleeps

Asked 2026-10-29 by the operator ("put it on Supabase or Vercel so it can run when my
computer is sleep"); AWS chosen 2026-10-30. Paper only. Checked 2026-10-29/30.

| # | Finding | Status | Source |
|---|---|---|---|
| H1 | Vercel Functions: max duration 300 s on Hobby; 800 s on Pro (1800 s beta). Cannot keep IB Gateway or the trading node alive. | VERIFIED | V1 |
| H2 | Supabase is hosted Postgres. It could store reports or replace TimescaleDB; it cannot run the node or the gateway. | VERIFIED (product scope) | S1 |
| H3 | AWS Lightsail Linux bundles with IPv4: $24/mo 4 GB 2 vCPU 80 GB 4 TB; **$44/mo 8 GB 2 vCPU 160 GB 5 TB**; $84/mo 16 GB 4 vCPU. Snapshots $0.05/GB-mo. Outbound transfer over the allowance is billed (example $0.09/GB, Ohio). | VERIFIED | A1 |
| H4 | Lightsail SSH firewall rule: "Restrict to IP address" plus "Allow Lightsail browser SSH/RDP". | VERIFIED | A2 |
| H5 | DigitalOcean Basic droplet 8 GiB / 4 vCPU / 160 GiB = $48/mo (comparison). Hetzner US prices rose 15 Jun 2026 (CPX31 US EUR 62.49). | VERIFIED (DO); third party (Hetzner) | D1, N1 |
| H6 | `tailscale serve <port>` shares a local port only inside the tailnet (Funnel is the public one); needs HTTPS certificates enabled. `--bg` flag: UNVERIFIED on the docs page. | VERIFIED / UNVERIFIED | T1 |
| H7 | IBKR: headless gateway "not supported" by IBKR, works in practice via IBC + Xvfb (our image). Weekly re-auth on Monday still needs the phone approval; one login at a time. | VERIFIED (doc 08 B5-B7) | doc 08 |

Decision: one Lightsail 8 GB instance (Ubuntu 24.04, Docker from Ubuntu packages) runs the
paper services; a cron routine (`scripts/server/flowforge-day.sh`) downloads the prior day's
ticks, starts the node at 08:30 and 09:20 ET and stops it at 16:10 ET. It never starts the
node with the kill switch or a halt set. 4 GB was judged too tight (the gateway's Java plus
the node plus image builds). Dashboard stays on 127.0.0.1, reached through Tailscale Serve.

Sources
- V1: https://vercel.com/docs/functions/limitations (page dated 2026-08-24, read 2026-10-29)
- S1: https://supabase.com/docs (Postgres database platform)
- A1: https://aws.amazon.com/lightsail/pricing/ (read 2026-10-30)
- A2: https://docs.aws.amazon.com/lightsail/latest/userguide/amazon-lightsail-editing-firewall-rules.html (read 2026-10-30)
- D1: https://www.digitalocean.com/pricing/droplets (read 2026-10-29)
- N1: https://northflank.com/blog/hetzner-cloud-server-price-increases (read 2026-10-29)
- T1: https://tailscale.com/docs/features/tailscale-serve (read 2026-10-30)
