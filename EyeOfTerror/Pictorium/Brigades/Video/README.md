# Video Brigade

Planned brigade for motion generation backends such as AnimateDiff, Stable Video
Diffusion, LTX, Wan, Hunyuan, or other future video engines.

Video is a separate backend class, not a DemonsForge image feature. This brigade
must own GPU scheduling, load/unload policy, frame/clip contracts, and
image-to-video or text-to-video verification.
