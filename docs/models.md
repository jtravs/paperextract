# Model licences

paperextract is Apache-2.0 and distributes no model weights. The backends
and their models are separate works with their own licences, which apply to
anyone who downloads and runs them: `paperextract models fetch` downloads
from the publishers' repositories, and using a backend means accepting its
model's terms. This page summarizes what those terms say as of 24 September
2026. It is a summary to help choose a backend, not legal advice; the
licence texts in the model repositories govern.

## Summary

| Set | Model | Licence | Notes |
| --- | --- | --- | --- |
| `mineru` | `opendatalab/MinerU-4_models_onnx` | Apache-2.0 (inferred) | The repository declares no licence; its README says the original licences apply, and its sources (PaddlePaddle PP-DocLayoutV2, PP-OCRv6, PP-FormulaNet and MinerU's torch models) declare Apache-2.0. |
| `mineru` | `jinzhenj/MinerU2.5-Pro-2605-1.2B-GGUF` | Apache-2.0 (inferred) | A third-party quantization with no declared licence, of `opendatalab/MinerU2.5-Pro-2605-1.2B`, which declares Apache-2.0. |
| `mineru-cuda` | `opendatalab/MinerU-4_models_torch` | Apache-2.0 | Declared on the model card. |
| `mineru-cuda` | `opendatalab/MinerU2.5-Pro-2605-1.2B` | Apache-2.0 | Declared on the model card. |
| `docling` | `docling-project/docling-layout-heron` | Apache-2.0 | Declared on the model card. |
| `docling` | `docling-project/docling-models` | CDLA-Permissive-2.0, Apache-2.0 | Declared on the model card. |
| `docling` | `docling-project/CodeFormulaV2` | CDLA-Permissive-2.0 | Declared on the model card. |
| `marker` | `datalab-to/surya-ocr-2-gguf`, `surya_layout2`, OCR error model | AI Pubs OpenRAIL-M, modified | Use restrictions, attribution and share-alike that extend to outputs; see below. |
| `marker` | llama.cpp b10964 server | MIT | |
| `describe` | `Qwen/Qwen3.8-27B` | Apache-2.0 | Licence file shipped with the weights. |

## The backend code

- **MinerU** (the default backend) is released under the MinerU Open Source
  License: Apache-2.0 with additional terms. Commercial use needs no separate
  licence unless you and your affiliates exceed 100 million monthly active
  users or USD 20 million total monthly revenue, and an online service built
  on it must give attribution.
- **Docling** is MIT.
- **Marker** code is Apache-2.0; its Surya models are licensed separately.
- **vLLM**, used for GPU inference, is Apache-2.0.

## Marker's Surya models

The Surya models carry a modified AI Pubs OpenRAIL-M licence (the `LICENSE`
file in each repository). In summary:

- **Use restrictions** apply to the model, its derivatives and its
  **output**: no use that breaks the law or infringes others' rights, and no
  commercial use by an organization (with its employer or affiliates) that
  had more than USD 5 million in gross revenue in the prior year or raised
  more than USD 5 million in funding, except personal or research use, and no
  use by anyone offering a product or service that competes with the
  licensor's. Broader licences are offered by the licensor.
- **Attribution**: using the output requires crediting the licensor, linking
  the model and providing the licence.
- **Share-alike**: the licence must be applied to copies and derivatives of
  the model and to the output and its derivatives.

A library extracted with `--backend marker` therefore carries these terms.
MinerU, the default, and Docling do not impose conditions on their output.

## Your papers

Extracted papers remain their publishers' or authors' copyrighted works.
paperextract keeps them locally; sharing a library is subject to the papers'
own terms.
