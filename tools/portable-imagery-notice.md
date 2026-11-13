# Imagery extraction model

VerdFlow runs a local ONNX export of [SatLens SegFormer-B0 by boehnen](https://github.com/boehnen/satlens).
The source checkpoint is the [Hugging Face revision 19cdd87](https://huggingface.co/boehnen/satlens-segformer/tree/19cdd879e535d76e7fcbbd1e8b54dac9c1da90d6), whose model card declares MIT.
The export adapts the inference graph to return the probabilities used by the geospatial adapter. It does not include training imagery or labels.

SatLens lists [OpenEarthMap](https://open-earth-map.org/overview_oem.html) as its training dataset.
OpenEarthMap documents different terms for different source images and labels; the upstream model card alone does not document those terms' applicability to redistribution of the weights.
The [SatLens training notebook](https://github.com/boehnen/satlens/blob/main/notebooks/train.ipynb) uses the existing SatLens checkpoint when available and otherwise initializes from [NVIDIA SegFormer MiT-B0](https://huggingface.co/nvidia/mit-b0#license). It does not establish the exact ancestry of the pinned checkpoint. The [NVIDIA terms](https://github.com/NVlabs/SegFormer/blob/master/LICENSE) linked by that model limit noncommercial use to research or evaluation, and impose conditions on redistribution and derivative works. Do not publish a bundle containing this export without a documented clearance for its intended use and distribution.
VerdFlow's Apache-2.0 license covers VerdFlow code, not the model.
