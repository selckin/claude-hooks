#!/usr/bin/env bash
exec journalctl --user -u claude-hooks -f -n 400 --output cat
