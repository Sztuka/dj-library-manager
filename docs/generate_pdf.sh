#!/bin/bash
# Script to generate PDF from Markdown documentation
# Requires: pandoc (https://pandoc.org/)

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
DOCS_DIR="$SCRIPT_DIR"
OUTPUT_DIR="$DOCS_DIR"

echo "Generating PDF from Markdown documentation..."

# Check if pandoc is installed
if ! command -v pandoc &> /dev/null; then
    echo "Error: pandoc is not installed."
    echo "Install it with: brew install pandoc (macOS) or apt-get install pandoc (Linux)"
    exit 1
fi

# Generate PDF from ARCHITECTURE.md
echo "Generating ARCHITECTURE.pdf..."
pandoc "$DOCS_DIR/ARCHITECTURE.md" \
    -o "$OUTPUT_DIR/ARCHITECTURE.pdf" \
    --pdf-engine=xelatex \
    -V geometry:margin=2cm \
    -V fontsize=11pt \
    --toc \
    --toc-depth=3 \
    --highlight-style=tango

# Generate PDF from QUICK_REFERENCE.md
echo "Generating QUICK_REFERENCE.pdf..."
pandoc "$DOCS_DIR/QUICK_REFERENCE.md" \
    -o "$OUTPUT_DIR/QUICK_REFERENCE.pdf" \
    --pdf-engine=xelatex \
    -V geometry:margin=2cm \
    -V fontsize=11pt \
    --highlight-style=tango

echo "✅ PDFs generated successfully!"
echo "Output files:"
echo "  - $OUTPUT_DIR/ARCHITECTURE.pdf"
echo "  - $OUTPUT_DIR/QUICK_REFERENCE.pdf"

