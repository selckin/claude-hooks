#!/usr/bin/env bash
exec journalctl --user -u claude-hooks -f --output cat
