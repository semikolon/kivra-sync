#!/usr/bin/python3
# -*- coding: utf-8 -*-

import io
import logging
import html

# weasyprint is an OPTIONAL dependency. It is imported lazily inside
# html_to_pdf() so the module (and the filesystem storage backend that imports
# it at top level) loads even on machines without weasyprint's native GTK libs
# (e.g. a plain Windows install). html_to_pdf() returns None when weasyprint is
# unavailable, and the filesystem backend then falls back to saving raw .html.
# Empirically (Fredrik's 902-letter corpus, 2026-06-12) only ~30 HTML-only
# letters exist and they are agency statements (SCB, Pensionsmyndigheten), not
# bills — so dropping PDF-rendering of HTML letters costs no obligation data.
_weasyprint_logging_configured = False


def _configure_weasyprint_logging():
    global _weasyprint_logging_configured
    if _weasyprint_logging_configured:
        return
    logger = logging.getLogger('weasyprint')
    logger.setLevel(logging.ERROR)
    logger.handlers = [logging.FileHandler('./weasyprint.log')]  # Remove the default stderr handler
    _weasyprint_logging_configured = True

def text_to_html(text_content, title=None):
    """
    Convert plain text to HTML with a clean, readable template.
    
    Args:
        text_content (str): Plain text content to convert
        title (str, optional): Title to display in the HTML
        
    Returns:
        str: HTML content
    """
    # Escape HTML special characters
    escaped_text = html.escape(text_content)
    
    # Replace newlines with <br> tags and preserve whitespace
    formatted_text = escaped_text.replace('\n', '<br>')
    
    # Create HTML with a clean template
    html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>{title or 'Document'}</title>
    <style>
        body {{
            font-family: Arial, sans-serif;
            line-height: 1.6;
            margin: 2cm;
            color: #333;
        }}
        .container {{
            max-width: 800px;
            margin: 0 auto;
        }}
        .header {{
            border-bottom: 1px solid #ddd;
            padding-bottom: 10px;
            margin-bottom: 20px;
        }}
        .content {{
            white-space: pre-wrap;
            font-family: monospace;
            background-color: #f9f9f9;
            padding: 15px;
            border-radius: 5px;
        }}
        .footer {{
            margin-top: 30px;
            font-size: 0.8em;
            color: #777;
            text-align: center;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>{title or 'Document'}</h1>
        </div>
        <div class="content">
{formatted_text}
        </div>
        <div class="footer">
            Converted from plain text
        </div>
    </div>
</body>
</html>"""
    
    return html_content

def html_to_pdf(html_content):
    """
    Convert HTML content to PDF.
    
    Args:
        html_content (str): HTML content to convert
        
    Returns:
        bytes: PDF content as bytes, or None if conversion failed
    """
    try:
        from weasyprint import HTML  # lazy: optional native dep (GTK)
    except Exception as e:
        logging.warning(
            f"weasyprint unavailable ({e}); HTML letters will be saved as raw .html "
            f"instead of rendered PDF. Install weasyprint + GTK to enable PDF rendering."
        )
        return None
    try:
        _configure_weasyprint_logging()
        pdf_buffer = io.BytesIO()
        HTML(string=html_content).write_pdf(pdf_buffer)
        return pdf_buffer.getvalue()
    except Exception as e:
        logging.error(f"Error converting HTML to PDF: {str(e)}")
        return None
