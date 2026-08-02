# Makefile for building the ACL paper PDFs from paper/paper.tex
#
#   make paper      Build BOTH versions of the paper:
#                     paper/paper.pdf        anonymised review build (line-number ruler)
#                     paper/paper_final.pdf  camera-ready build (named authors, no ruler)
#   make review     Build only the anonymised review PDF (paper/paper.pdf)
#   make final      Build only the camera-ready PDF     (paper/paper_final.pdf)
#   make tex        Alias for `make review`
#   make clean      Remove LaTeX build artifacts (keeps the PDFs)
#   make distclean  Remove build artifacts and the PDFs

PAPER_DIR := paper
MAIN      := paper
FINAL     := paper_final

PDFLATEX  := pdflatex -interaction=nonstopmode -halt-on-error
BIBTEX    := bibtex

.PHONY: paper review final tex clean distclean

## Build both versions from the single source. The pdflatex -> bibtex ->
## pdflatex -> pdflatex sequence is the standard incantation that resolves
## citations and cross-references.
paper: review final

## Anonymised review build -> paper/paper.pdf.
## \ifcameraready defaults to false, so acl loads with [review] and the ruler.
review:
	cd $(PAPER_DIR) && $(PDFLATEX) $(MAIN).tex
	cd $(PAPER_DIR) && $(BIBTEX) $(MAIN)
	cd $(PAPER_DIR) && $(PDFLATEX) $(MAIN).tex
	cd $(PAPER_DIR) && $(PDFLATEX) $(MAIN).tex
	@echo "Built $(PAPER_DIR)/$(MAIN).pdf (anonymised review)"

## Camera-ready build -> paper/paper_final.pdf.
## Defining \CameraReady before \input flips the style option and the author
## block; -jobname routes every aux/bbl/pdf to paper_final.* so both PDFs
## coexist in paper/ from the one .tex.
final:
	cd $(PAPER_DIR) && $(PDFLATEX) -jobname=$(FINAL) "\def\CameraReady{}\input{$(MAIN)}"
	cd $(PAPER_DIR) && $(BIBTEX) $(FINAL)
	cd $(PAPER_DIR) && $(PDFLATEX) -jobname=$(FINAL) "\def\CameraReady{}\input{$(MAIN)}"
	cd $(PAPER_DIR) && $(PDFLATEX) -jobname=$(FINAL) "\def\CameraReady{}\input{$(MAIN)}"
	cd $(PAPER_DIR) && $(PDFLATEX) -jobname=$(FINAL) "\def\CameraReady{}\input{$(MAIN)}"
	@echo "Built $(PAPER_DIR)/$(FINAL).pdf (camera-ready)"

tex: review

clean:
	cd $(PAPER_DIR) && rm -f \
		$(MAIN).aux  $(MAIN).bbl  $(MAIN).blg  $(MAIN).log  $(MAIN).out  $(MAIN).toc  $(MAIN).fls  $(MAIN).fdb_latexmk \
		$(FINAL).aux $(FINAL).bbl $(FINAL).blg $(FINAL).log $(FINAL).out $(FINAL).toc $(FINAL).fls $(FINAL).fdb_latexmk

distclean: clean
	cd $(PAPER_DIR) && rm -f $(MAIN).pdf $(FINAL).pdf
