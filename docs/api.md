# API

The package exposes the command line, its configuration, and the ingest,
inspection, worker, normalization, publication and identity APIs it is built on.
In constructor signatures, `...` denotes a default created for each instance.

```{eval-rst}
.. automodule:: paperextract
```

```{eval-rst}
.. automodule:: paperextract.cli
   :members:

.. autodata:: paperextract.cli.EXIT_CODES
```

```{eval-rst}
.. automodule:: paperextract.config
   :members:
```

```{eval-rst}
.. automodule:: paperextract.reprocess
   :members:
```

```{eval-rst}
.. automodule:: paperextract.crops
   :members:

.. autodata:: paperextract.crops.RENDER_VERSION
```

```{eval-rst}
.. automodule:: paperextract.attachments
   :members:
```

```{eval-rst}
.. automodule:: paperextract.catalog
   :members:

.. autodata:: paperextract.catalog.LAYOUTS
```

```{eval-rst}
.. automodule:: paperextract.index
   :members:
```

```{eval-rst}
.. automodule:: paperextract.benchmark
   :members:
```

```{eval-rst}
.. automodule:: paperextract.ingest
   :members:
```

```{eval-rst}
.. automodule:: paperextract.pdf
   :members:
```

```{eval-rst}
.. automodule:: paperextract.protocol
   :members:

.. py:type:: Profile

   Union of :class:`MineruProfile`, :class:`DoclingProfile` and :class:`MarkerProfile`.
```

```{eval-rst}
.. automodule:: paperextract.worker
   :members:
```

```{eval-rst}
.. automodule:: paperextract.document
   :members:
```

```{eval-rst}
.. automodule:: paperextract.tables
   :members:
```

```{eval-rst}
.. automodule:: paperextract.normalize
   :members:
```

```{eval-rst}
.. automodule:: paperextract.normalize_docling
   :members:
```

```{eval-rst}
.. automodule:: paperextract.normalize_marker
   :members:
```

```{eval-rst}
.. automodule:: paperextract.compare
   :members:
```

```{eval-rst}
.. automodule:: paperextract.formats
   :members:
```

```{eval-rst}
.. automodule:: paperextract.models
   :members:
```

```{eval-rst}
.. automodule:: paperextract.layout
   :members:
```

```{eval-rst}
.. automodule:: paperextract.table_ocr
   :members:

.. autodata:: paperextract.table_ocr.MIN_TABLE_OVERLAP
```

```{eval-rst}
.. automodule:: paperextract.table_check
   :members:
```

```{eval-rst}
.. automodule:: paperextract.supplements
   :members:
```

```{eval-rst}
.. automodule:: paperextract.pipeline
   :members:
```

```{eval-rst}
.. automodule:: paperextract.describe
   :members:

.. autodata:: paperextract.describe.MACHINE_NOTICE

.. autodata:: paperextract.describe.MIN_INFORMATIVE_CHARACTERS

.. autodata:: paperextract.describe.PROMPT_VERSIONS

.. autodata:: paperextract.describe.MAX_CONTEXT_CHARACTERS

.. autodata:: paperextract.describe.MIN_REGION_CHARACTERS
```

```{eval-rst}
.. automodule:: paperextract.export
   :members:

.. autodata:: paperextract.export.DESCRIPTION_BEGIN

.. autodata:: paperextract.export.DESCRIPTION_END
```

```{eval-rst}
.. automodule:: paperextract.storage
   :members:
```

```{eval-rst}
.. automodule:: paperextract.registry
   :members:

.. py:type:: Lookup

   Callable taking a DOI string and returning :class:`RegistryRecord` or
   :class:`RegistryFailure`.

.. py:type:: Search

   Callable taking a bibliographic query and returning a tuple of
   :class:`RegistryRecord` objects or :class:`RegistryFailure`.
```

```{eval-rst}
.. automodule:: paperextract.identity
   :members:
```

```{eval-rst}
.. automodule:: paperextract.bibtex
   :members:
```

```{eval-rst}
.. automodule:: paperextract.fields
   :members:
```

```{eval-rst}
.. autoclass:: paperextract.describers.Describer
   :members:

.. autoclass:: paperextract.describers.DescribeRequest
   :members:

.. autoclass:: paperextract.describers.ModelReply
   :members:
```
