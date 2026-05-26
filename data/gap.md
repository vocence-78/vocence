# Known Gap: chute_config.yml Dependency Swap

## Summary

`chute_config.yml` is replaceable by miners. It controls pip dependencies installed
at chute build time. A miner could fork the `qwen_tts` Python package, modify
`Qwen3TTSModel.from_pretrained()` or `generate_voice_design()` internally (e.g. add
speaker conditioning, custom decoding, pre/post processing), publish the forked
package, and change `chute_config.yml` to install it.

The locked `miner.py` calls `from qwen_tts import Qwen3TTSModel` -- if the underlying
package is swapped, the behavior changes even though `miner.py` is byte-identical.

## Impact

Medium. Requires publishing a forked package and modifying the chute build config.
The locked `miner.py` + wrapper integrity still make the attack surface narrow and
detectable.

## Possible Mitigations (not yet implemented)

1. Pin the `qwen_tts` package version or hash in validation -- reject repos whose
   `chute_config.yml` installs an unrecognized version.
2. Lock `chute_config.yml` pip entries against an approved allowlist.
3. Check the installed `qwen_tts` package hash at chute startup (runtime check).
4. Accept the gap -- it is a high-effort attack that is detectable through manual
   review of deployed chute configs.
