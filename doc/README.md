# Documentation for alibi

This directory contains the sources (`.md` and `.ipynb` files) for the
documentation. The main index page is defined in `source/index.md`.
The Sphinx options and plugins are found in the `source/conf.py` file.
The documentation is generated in full by calling `make html` which
also automatically generates the Python API documentation from
docstrings.

## Building documentation locally
To build the documentation, first we need to install Python requirements:

`pip install -r ../requirements/docs.txt`

We also need `pandoc` for parsing Jupyter notebooks, the easiest way
to install this is using conda:

`conda install -c conda-forge pandoc=1.19.2`

Note: the older version of pandoc is used because this is available on `readthedocs.org` where we host our docs, the newer version fixes a lot of bugs, but using a newer version locally is misleading as to whether the docs will render properly on RTD, also see [gotchas](#gotchas-when-writing-notebooks-as-examples).

We are now ready to build the docs:

`make -C .. build_docs`

This calls the sphinx html builder command defined in the main repo [Makefile](../Makefile). Note this can take some time as some of the notebooks may be executed
during the build process. The resulting documentation is located in the
`_build` directory with `_build/html/index.html` marking the homepage. 

A pdf of the docs can also be built:

`make -C .. build_latex`

with the resulting pdf file residing at `_build/latex/alibi.pdf`.

## Sphinx extensions and plugins
We use various Sphinx extensions and plugins to build the documentation, including:
 * [myst-parser](https://myst-parser.readthedocs.io/en/latest/) - to allow `rst` directives in Markdown files (see [here](#myst-format))
 * [sphinx.ext.napoleon](https://www.sphinx-doc.org/en/master/usage/extensions/napoleon.html) - support extracting Numpy style doctrings for API doc generation
 * [sphinx_autodoc_typehints](https://github.com/agronholm/sphinx-autodoc-typehints) - support parsing of typehints for API doc generation
 * [sphinxcontrib.apidoc](https://github.com/sphinx-contrib/apidoc) - automatic running of [sphinx-apidoc](https://www.sphinx-doc.org/en/master/man/sphinx-apidoc.html) during the build to document API
 * [nbsphinx](https://nbsphinx.readthedocs.io) - parsing Jupyter notebooks to generate static documentation
 * [nbsphinx_link](https://nbsphinx-link.readthedocs.io) - support linking to notebooks outside of Sphinx source directory via `.nblink` files

The full list of plugins and their options can be found in `source/conf.py`.

## Adding new examples
All examples are Jupyter notebooks and live in the top level `examples` directory. To make them available as documentation, create an `.nblink` file under `doc/source/examples` which is a piece of json pointing to the `.ipynb` example notebook. E.g. if there is a notebook called `examples/notebook.ipynb`, then create a file `doc/source/examples/notebook.nblink` with the following contents:
```json
{
  "path": "../../../examples/notebook.ipynb"
}
```
From here on you can link and refer to the `notebook.ipynb` elsewhere in the documentation as if it lived under `doc/source/examples`.

## MyST format

For consistency, all docs files excluding `.ipynb` notebooks are written as Markedly Structured Text (MyST) files. 
Although still named as `.md` files, the files are passed through [myst-parser](https://myst-parser.readthedocs.io/en/latest/), 
which processes `rst`-type directives contained within the `.md` files. This allows for more powerful functionality 
to be included in the docs. For example, an admonition block can be included in a `.md` file with:

````md
```{admonition} My markdown link
Here is [markdown link syntax](https://jupyter.org)
```
````

and an image can be embedded with:

````md
```{image} img/fun-fish.png
:alt: fishy
:class: bg-primary
:width: 200px
:align: center
```
````

For more details on the MyST syntax see the [MyST docs](https://myst-parser.readthedocs.io/en/latest/syntax/syntax.html). 

## Gotchas when writing notebooks as examples
We use Jupyter notebooks for examples and method descriptions and invoke the [nbsphinx](https://nbsphinx.readthedocs.io) plugin, which in turn invokes `pandoc` for rendering the notebooks as static documentation. Generally, the Jupyter notebook is more permissive for what it can render correctly than the static documentation, so it is important to check that the content is rendered correctly in the static docs as well. Here is a list of common formatting gotchas and how to fix them:
* When using a bullet-point list, leave a blank line before the preceding paragraph, otherwise it will fail to render
* Always use `$$ $$` or `\begin{equation}\end{equation}`to delimit display math.
* Leave a blank line before and after any display math
* For references and footnotes, the tag indicating the section needs to start with an uppercase letter, e.g. `[[1]](#References)` linking to a section `<a id='References'></a>
[1](#f_1) reference here`
* Whilst superscript (for e.g. footnotes) can be rendered in Jupyter using `<sup></sup>` tags, this won't work in the static docs. To avoid jarring appearence of footnote numbers in the text, wrap them in parentheses, e.g. <sup>`(1)`</sup> will be rendered inline as `(1)`.
* Avoid starting a cell with an html tag, e.g. for making hyperlinks to link back to the reference in the text `<a id='ref1'></a>`. The (older) version of `pandoc==1.19.2` used both on CI and `readthedocs.org` machines cannot handle it and may fail to build the docs. Recommended action is to put such tags at the end of the cell.
* Avoid using underscores in alternative text for images, e.g. instead of `![my_pic](my_pic.png)` use `![my-pic](my_pic.png)`, this is due to the old version of `pandoc==1.19.2` used on `reathedocs.org`.
* Avoid nesting markdown markups, e.g. italicising a hyperlink, this might not render
* ~~When embedding images in notebooks which are linked to via a `.nblink` file, an `extra-media` key needs to be added in the `.nblink` file. See the [nbsphinx-link](https://github.com/vidartf/nbsphinx-link) docs, or [alibi_detect_deploy.nblink](https://github.com/SeldonIO/alibi-detect/blob/master/doc/source/examples/alibi_detect_deploy.nblink) for an example in alibi-detect.~~ Prefer using the following to produce self-contained notebooks:
* To add a static image to an example, use the syntax `![my-image.png](attachment:my_image.png)`. Ensure the image is located in the `examples/` folder. This will embed the actual binary image into the example notebook so that the notebook is self-contained and renders properly on the static docs.
