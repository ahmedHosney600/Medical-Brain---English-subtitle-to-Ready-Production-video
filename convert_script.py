#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Script Exporter: Markdown -> Responsive HTML, PDF & DOCX
Converts Arabic/English video production scripts into:
1. Highly styled, responsive HTML with per-screen font size controls (Mobile, Tablet, Small PC, Large PC),
   reading/teleprompter modes, and quick-copy tools.
2. High-fidelity PDF export via Chrome/Chromium headless rendering.
3. Formatted Microsoft Word (.docx) documents with full Arabic RTL support, production scene badges,
   director cues, speaker tags, copyable blocks, and styled metadata tables.

Can be run automatically in workflow or standalone via terminal.
"""

import os
import sys
import re
import shutil
import subprocess
import zipfile
import xml.sax.saxutils as saxutils
from datetime import datetime, timezone
from pathlib import Path


def find_chrome_executable():
    """Locate Google Chrome, Chromium, Brave, or Edge on macOS, Linux, or Windows."""
    # Common fixed paths
    candidates = [
        # macOS
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        os.path.expanduser("~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        # Linux
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        # Windows
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ]
    for path in candidates:
        if os.path.isfile(path) and (os.access(path, os.X_OK) or sys.platform.startswith("win")):
            return path

    # Search in system PATH
    for name in ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"]:
        found = shutil.which(name)
        if found:
            return found

    return None


def export_html_to_pdf(html_path, pdf_path):
    """Render HTML to PDF using Chrome/Chromium headless mode with proper URI encoding."""
    chrome_bin = find_chrome_executable()
    if not chrome_bin:
        print("⚠️ Chrome/Chromium executable not found. PDF export was skipped.")
        print("   To generate PDF, install Google Chrome or Chromium.")
        return None

    abs_html = os.path.abspath(html_path)
    abs_pdf = os.path.abspath(pdf_path)
    file_uri = Path(abs_html).resolve().as_uri()

    cmd = [
        chrome_bin,
        "--headless=new",
        "--disable-gpu",
        "--no-sandbox",
        "--no-pdf-header-footer",
        "--virtual-time-budget=3000",
        "--run-all-compositor-stages-before-draw",
        f"--print-to-pdf={abs_pdf}",
        file_uri
    ]

    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if res.returncode == 0 and os.path.exists(abs_pdf) and os.path.getsize(abs_pdf) > 0:
            return abs_pdf
        else:
            # Fallback without --headless=new for older chromium versions
            fallback_cmd = [
                chrome_bin,
                "--headless",
                "--disable-gpu",
                "--no-sandbox",
                "--print-to-pdf-no-header",
                f"--print-to-pdf={abs_pdf}",
                file_uri
            ]
            res2 = subprocess.run(fallback_cmd, capture_output=True, text=True, timeout=60)
            if res2.returncode == 0 and os.path.exists(abs_pdf) and os.path.getsize(abs_pdf) > 0:
                return abs_pdf
            print(f"⚠️ PDF generation returned code {res.returncode}. Stderr: {res.stderr[:200]}")
            return None
    except Exception as e:
        print(f"⚠️ PDF generation error: {e}")
        return None


def enhance_markdown_content(md_text):
    """
    Apply structural styling enhancements to the script markdown
    before/after converting to HTML:
    - Highlight speaker cues
    - Highlight director/camera cues
    - Format scene markers [HOOK], [INTRO], etc.
    - Wrap tables for responsive horizontal scrolling
    """
    try:
        import markdown
    except ImportError:
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "markdown", "pymdown-extensions"])
        except subprocess.CalledProcessError:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "--break-system-packages", "markdown", "pymdown-extensions"])
        import markdown

    # Extract title from markdown if available
    title_match = re.search(r'Video Title.*?[|:]\s*([^\n|]+)', md_text)
    doc_title = title_match.group(1).strip() if title_match else "نص الفيديو - Video Script Package"

    # Pre-process markers
    processed_md = md_text

    # Convert markdown to HTML using extensions
    extensions = [
        'tables',
        'fenced_code',
        'toc',
        'sane_lists',
        'attr_list',
        'nl2br'
    ]
    
    # Try adding pymdownx if installed
    try:
        import pymdownx.superfences
        extensions.append('pymdownx.superfences')
    except ImportError:
        pass

    raw_html = markdown.markdown(processed_md, extensions=extensions)

    # Post-process HTML for enhanced script elements:
    
    # 1. Wrap tables in responsive wrapper
    raw_html = re.sub(
        r'(<table>.*?</table>)',
        r'<div class="table-responsive">\1</div>',
        raw_html,
        flags=re.DOTALL
    )

    # 2. Format Scene Markers like **[HOOK]**, **[INTRO]**, **[SCENE 1]**
    raw_html = re.sub(
        r'<p><strong>\[(.*?)\]</strong></p>',
        r'<div class="scene-header"><span class="scene-badge"><span class="badge-icon">⚡</span> \1</span></div>',
        raw_html
    )

    # 3. Format Director & Camera Cues e.g. **(الكاميرا قريبة...)** or *(موسيقى تشويقية...)*
    def replace_cue(match):
        inner = match.group(1).strip()
        return f'<div class="director-cue"><span class="cue-icon">🎬</span> {inner}</div>'

    raw_html = re.sub(r'<p><strong>\(([\u0600-\u06FFa-zA-Z0-9\s\.,!\?؟/:\-–—"\']+)\)</strong></p>', replace_cue, raw_html)
    raw_html = re.sub(r'<p><em>\(([\u0600-\u06FFa-zA-Z0-9\s\.,!\?؟/:\-–—"\']+)\)</em></p>', replace_cue, raw_html)

    # 4. Highlight Speaker Dialogue e.g. <strong>د. أحمد:</strong>
    raw_html = re.sub(
        r'<strong>(د\.\s*[\u0600-\u06FFa-zA-Z]+|المذيع|الراوي|المريض|Dr\.\s*[\w]+):</strong>',
        r'<span class="speaker-tag"><span class="speaker-avatar">🎙️</span> \1:</span>',
        raw_html
    )

    # 5. Add copy buttons and styling to code blocks (pre > code)
    def wrap_pre(match):
        code_content = match.group(1)
        return (
            '<div class="code-card">'
            '  <div class="code-card-header">'
            '    <span class="code-tag">نص قابل للنسخ / Copyable Content</span>'
            '    <button class="copy-button" onclick="copyCodeBlock(this)">'
            '      <span class="btn-icon">📋</span> <span class="btn-text">نسخ النص</span>'
            '    </button>'
            '  </div>'
            f'  <pre><code>{code_content}</code></pre>'
            '</div>'
        )

    raw_html = re.sub(r'<pre><code>(.*?)</code></pre>', wrap_pre, raw_html, flags=re.DOTALL)

    # 6. Strip leading <hr /> or empty lines at top
    raw_html = re.sub(r'^\s*<hr\s*/?>\s*', '', raw_html.strip())

    return doc_title, raw_html


def build_full_html_document(title, body_content):
    """
    Constructs an ultra-premium, responsive HTML file with:
    - Dedicated screen-size font controls:
      * Mobile (< 640px)
      * Tablet (640px - 1023px)
      * Small Screen PC / Laptop (1024px - 1439px)
      * Large Screen PC (≥ 1440px)
    - Real-time CSS Custom Properties
    - Interactive Settings Bar (collapsible & persistent in localStorage)
    - Device Viewport Simulator (test Mobile, Tablet, PC sizes right on screen)
    - Teleprompter / High-Contrast Filming Mode with Auto-Scroll
    - Dark / Light / Teleprompter Theme Switcher
    - Full Arabic RTL Layout with Cairo Google Font
    - Clean Print / PDF Styling
    """
    html_template = f"""<!DOCTYPE html>
<html lang="ar" dir="rtl" data-theme="dark">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=5.0">
  <title>{title}</title>
  
  <!-- Modern Arabic Google Fonts -->
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Amiri:ital,wght@0,400;0,700;1,400&family=Cairo:wght@300;400;500;600;700;800;900&family=JetBrains+Mono:wght@400;500;600&family=Tajawal:wght@400;500;700;800&display=swap" rel="stylesheet">

  <style>
    /* ==========================================================================
       CSS VARIABLES & RESPONSIVE FONT SCALING
       ========================================================================== */
    :root {{
      /* Font sizes for each responsive breakpoint (User-Controllable) */
      --fs-mobile: 16px;        /* Mobile: < 640px */
      --fs-tablet: 18px;        /* Tablet: 640px - 1023px */
      --fs-laptop: 20px;        /* Small PC / Laptop: 1024px - 1439px */
      --fs-desktop: 22px;       /* Large Screen PC: >= 1440px */
      
      --font-family: 'Cairo', system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      --font-mono: 'JetBrains Mono', monospace;
      --line-height: 1.8;
      
      /* Dark Theme Colors (Default) */
      --bg-primary: #0b0f19;
      --bg-secondary: #131b2e;
      --bg-card: #182238;
      --bg-card-hover: #1e2b46;
      --border-color: #243452;
      --border-subtle: #1a2842;
      
      --text-main: #f1f5f9;
      --text-muted: #94a3b8;
      --text-dim: #64748b;
      
      --accent-primary: #38bdf8;
      --accent-hover: #0ea5e9;
      --accent-glow: rgba(56, 189, 248, 0.25);
      
      --badge-gold: #f59e0b;
      --badge-emerald: #10b981;
      --badge-rose: #f43f5e;
      --badge-purple: #a855f7;
      
      --container-width: 1080px;
      --radius-sm: 8px;
      --radius-md: 14px;
      --radius-lg: 20px;
      --shadow-card: 0 10px 25px -5px rgba(0, 0, 0, 0.4), 0 8px 10px -6px rgba(0, 0, 0, 0.3);
      --transition-smooth: all 0.25s cubic-bezier(0.16, 1, 0.3, 1);
    }}

    /* Light Theme Overrides */
    [data-theme="light"] {{
      --bg-primary: #f8fafc;
      --bg-secondary: #f1f5f9;
      --bg-card: #ffffff;
      --bg-card-hover: #f8fafc;
      --border-color: #cbd5e1;
      --border-subtle: #e2e8f0;
      
      --text-main: #0f172a;
      --text-muted: #475569;
      --text-dim: #94a3b8;
      
      --accent-primary: #0284c7;
      --accent-hover: #0369a1;
      --accent-glow: rgba(2, 132, 199, 0.2);
      
      --shadow-card: 0 10px 25px -5px rgba(15, 23, 42, 0.08), 0 8px 10px -6px rgba(15, 23, 42, 0.04);
    }}

    /* Teleprompter / High-Contrast Filming Mode */
    [data-theme="teleprompter"] {{
      --bg-primary: #000000;
      --bg-secondary: #0a0a0a;
      --bg-card: #111111;
      --bg-card-hover: #161616;
      --border-color: #333333;
      --border-subtle: #222222;
      
      --text-main: #fef08a; /* High-vis soft yellow */
      --text-muted: #e2e8f0;
      --text-dim: #94a3b8;
      
      --accent-primary: #facc15;
      --accent-hover: #eab308;
      --accent-glow: rgba(250, 204, 21, 0.3);
      
      --line-height: 2.2;
    }}

    /* ==========================================================================
       BASE RESET & TYPOGRAPHY
       ========================================================================== */
    html, body {{
      box-sizing: border-box;
      margin: 0;
      padding: 0;
      width: 100%;
      max-width: 100vw;
      overflow-x: hidden;
    }}

    *, *::before, *::after {{
      box-sizing: border-box;
      margin: 0;
      padding: 0;
    }}

    body {{
      font-family: var(--font-family);
      line-height: var(--line-height);
      color: var(--text-main);
      background-color: var(--bg-primary);
      transition: background-color 0.3s ease, color 0.3s ease;
      -webkit-font-smoothing: antialiased;
      -moz-osx-font-smoothing: grayscale;
      overflow-x: hidden;
      direction: rtl;
      text-align: right;
    }}

    /* Selection */
    ::selection {{
      background: var(--accent-primary);
      color: #0b0f19;
    }}

    /* ==========================================================================
       CONTAINER & HEADER
       ========================================================================== */
    .site-wrapper {{
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      width: 100%;
      max-width: 100%;
      min-width: 0;
    }}

    .site-header {{
      background: var(--bg-secondary);
      border-bottom: 1px solid var(--border-color);
      padding: 20px 24px;
      position: sticky;
      top: 0;
      z-index: 100;
      backdrop-filter: blur(12px);
      -webkit-backdrop-filter: blur(12px);
    }}

    .header-content {{
      max-width: var(--container-width);
      margin: 0 auto;
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 16px;
    }}

    .header-title-group {{
      display: flex;
      align-items: center;
      gap: 12px;
    }}

    .header-logo {{
      width: 44px;
      height: 44px;
      background: linear-gradient(135deg, #0284c7, #38bdf8);
      border-radius: var(--radius-md);
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 24px;
      box-shadow: 0 4px 12px var(--accent-glow);
    }}

    .header-title {{
      font-size: 1.15rem;
      font-weight: 800;
      color: var(--text-main);
      line-height: 1.3;
    }}

    .header-subtitle {{
      font-size: 0.85rem;
      color: var(--text-muted);
    }}

    .header-actions {{
      display: flex;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
    }}

    .btn {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 8px 16px;
      border-radius: var(--radius-sm);
      font-family: inherit;
      font-size: 0.9rem;
      font-weight: 600;
      cursor: pointer;
      border: 1px solid transparent;
      transition: var(--transition-smooth);
      background: var(--bg-card);
      color: var(--text-main);
      border-color: var(--border-color);
    }}

    .btn:hover {{
      background: var(--bg-card-hover);
      border-color: var(--accent-primary);
      transform: translateY(-1px);
    }}

    .btn-primary {{
      background: var(--accent-primary);
      color: #0b0f19;
      border-color: var(--accent-primary);
    }}

    .btn-primary:hover {{
      background: var(--accent-hover);
    }}

    .main-container {{
      max-width: var(--container-width);
      width: 100%;
      min-width: 0;
      margin: 0 auto;
      flex: 1;
    }}

    .script-hero-banner {{
      background: linear-gradient(135deg, rgba(56, 189, 248, 0.12), rgba(168, 85, 247, 0.08));
      border: 1px solid var(--border-color);
      border-radius: var(--radius-lg);
      padding: 24px 28px;
      margin-bottom: 30px;
      box-shadow: var(--shadow-card);
      min-width: 0;
    }}

    .hero-badge {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      font-size: 0.82rem;
      font-weight: 700;
      color: var(--accent-primary);
      background: rgba(56, 189, 248, 0.12);
      border: 1px solid rgba(56, 189, 248, 0.25);
      padding: 4px 12px;
      border-radius: 20px;
      margin-bottom: 12px;
    }}

    .hero-title {{
      margin: 0;
      font-size: 1.6rem;
      border-bottom: none;
      padding-bottom: 0;
      color: var(--text-main);
    }}

    /* ==========================================================================
       TYPOGRAPHY & CONTENT STYLING
       ========================================================================== */
    h1, h2, h3, h4, h5, h6 {{
      color: var(--text-main);
      font-weight: 800;
      line-height: 1.35;
      margin-top: 1.8em;
      margin-bottom: 0.8em;
      letter-spacing: -0.01em;
      word-break: break-word;
    }}

    h1 {{
      font-size: 1.85rem;
      color: var(--accent-primary);
      border-bottom: 2px solid var(--border-color);
      padding-bottom: 0.4em;
    }}

    h2 {{
      font-size: 1.55rem;
      display: flex;
      align-items: center;
      gap: 10px;
      border-right: 4px solid var(--accent-primary);
      padding-right: 12px;
      background: linear-gradient(90deg, rgba(56, 189, 248, 0.08), transparent);
      padding-top: 8px;
      padding-bottom: 8px;
      border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
    }}

    h3 {{
      font-size: 1.3rem;
      color: #38bdf8;
    }}

    h4 {{
      font-size: 1.15rem;
      color: #e2e8f0;
    }}

    p {{
      margin-bottom: 1.2em;
      color: var(--text-main);
      word-break: break-word;
    }}

    hr {{
      border: 0;
      height: 1px;
      background: linear-gradient(90deg, transparent, var(--border-color), transparent);
      margin: 2.5em 0;
    }}

    ul, ol {{
      margin-bottom: 1.4em;
      padding-right: 1.6em;
    }}

    li {{
      margin-bottom: 0.5em;
    }}

    strong {{
      font-weight: 700;
      color: #ffffff;
    }}

    [data-theme="light"] strong {{
      color: #0f172a;
    }}

    /* ==========================================================================
       TABLES (RESPONSIVE & BEAUTIFIED)
       ========================================================================== */
    .table-responsive {{
      width: 100%;
      max-width: 100%;
      min-width: 0;
      overflow-x: auto;
      -webkit-overflow-scrolling: touch;
      margin: 1.6em 0;
      border-radius: var(--radius-md);
      box-shadow: var(--shadow-card);
      border: 1px solid var(--border-color);
      background: var(--bg-card);
    }}

    table {{
      width: 100%;
      min-width: 480px;
      border-collapse: collapse;
      text-align: right;
      font-size: 0.95rem;
    }}

    th, td {{
      padding: 12px 18px;
      border-bottom: 1px solid var(--border-subtle);
    }}

    th {{
      background: var(--bg-secondary);
      color: var(--accent-primary);
      font-weight: 700;
      white-space: nowrap;
      position: sticky;
      top: 0;
    }}

    tr:last-child td {{
      border-bottom: none;
    }}

    tbody tr:hover {{
      background: var(--bg-card-hover);
    }}

    /* ==========================================================================
       SCRIPT PRODUCTION ELEMENTS
       ========================================================================== */
    /* Speaker tags */
    .speaker-tag {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      font-weight: 800;
      color: var(--accent-primary);
      background: rgba(56, 189, 248, 0.12);
      border: 1px solid rgba(56, 189, 248, 0.3);
      padding: 2px 10px;
      border-radius: 6px;
      margin-left: 6px;
    }}

    .speaker-avatar {{
      font-size: 1.1em;
    }}

    /* Director & Camera Cues */
    .director-cue {{
      background: rgba(245, 158, 11, 0.08);
      border-right: 4px solid var(--badge-gold);
      padding: 12px 16px;
      border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
      margin: 14px 0;
      font-size: 0.92rem;
      color: #fde047;
      font-style: italic;
      display: flex;
      align-items: flex-start;
      gap: 10px;
    }}

    [data-theme="light"] .director-cue {{
      background: #fef9c3;
      color: #854d0e;
      border-right-color: #ca8a04;
    }}

    .cue-icon {{
      font-style: normal;
      font-size: 1.2rem;
    }}

    /* Scene markers */
    .scene-header {{
      margin: 28px 0 16px 0;
    }}

    .scene-badge {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      background: linear-gradient(135deg, #a855f7, #ec4899);
      color: #ffffff;
      font-weight: 800;
      font-size: 0.85rem;
      letter-spacing: 0.05em;
      padding: 6px 14px;
      border-radius: 20px;
      text-transform: uppercase;
      box-shadow: 0 4px 12px rgba(168, 85, 247, 0.3);
    }}

    /* Code cards with copy button */
    .code-card {{
      background: var(--bg-secondary);
      border: 1px solid var(--border-color);
      border-radius: var(--radius-md);
      margin: 18px 0;
      overflow: hidden;
      box-shadow: var(--shadow-card);
    }}

    .code-card-header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 8px 16px;
      background: rgba(0, 0, 0, 0.2);
      border-bottom: 1px solid var(--border-subtle);
    }}

    .code-tag {{
      font-size: 0.8rem;
      font-family: var(--font-mono);
      color: var(--text-dim);
      direction: ltr;
    }}

    .copy-button {{
      background: var(--bg-card);
      border: 1px solid var(--border-color);
      color: var(--text-main);
      padding: 4px 12px;
      border-radius: 6px;
      font-family: inherit;
      font-size: 0.8rem;
      font-weight: 600;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      transition: var(--transition-smooth);
    }}

    .copy-button:hover {{
      border-color: var(--accent-primary);
      color: var(--accent-primary);
    }}

    .copy-button.copied {{
      background: var(--badge-emerald);
      color: #ffffff;
      border-color: var(--badge-emerald);
    }}

    pre {{
      padding: 16px;
      overflow-x: auto;
      font-family: var(--font-mono);
      font-size: 0.9rem;
      line-height: 1.6;
      color: #e2e8f0;
      direction: ltr;
      text-align: left;
    }}

    [data-theme="light"] pre {{
      background: #f1f5f9;
      color: #0f172a;
    }}

    code {{
      font-family: var(--font-mono);
    }}

    /* ==========================================================================
       FLOATING TOOLBAR & CONTROLS
       ========================================================================== */
    .floating-pill {{
      position: fixed;
      bottom: 24px;
      right: 24px;
      z-index: 999;
      display: flex;
      align-items: center;
      gap: 8px;
      background: var(--bg-secondary);
      border: 1px solid var(--border-color);
      padding: 8px 12px;
      border-radius: 40px;
      box-shadow: 0 10px 30px rgba(0, 0, 0, 0.5);
      backdrop-filter: blur(16px);
      -webkit-backdrop-filter: blur(16px);
    }}

    .pill-btn {{
      background: transparent;
      border: 1px solid transparent;
      color: var(--text-main);
      padding: 6px 12px;
      border-radius: 20px;
      font-family: inherit;
      font-size: 0.88rem;
      font-weight: 700;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      transition: var(--transition-smooth);
    }}

    .pill-btn:hover {{
      background: var(--bg-card);
      border-color: var(--border-color);
    }}

    .pill-btn-primary {{
      background: var(--accent-primary);
      color: #0b0f19;
    }}

    .pill-btn-primary:hover {{
      background: var(--accent-hover);
    }}

    /* ==========================================================================
       SETTINGS MODAL / DRAWER
       ========================================================================== */
    .settings-overlay {{
      display: none;
      position: fixed;
      top: 0;
      left: 0;
      right: 0;
      bottom: 0;
      background: rgba(0, 0, 0, 0.65);
      z-index: 1000;
      backdrop-filter: blur(6px);
      -webkit-backdrop-filter: blur(6px);
      align-items: center;
      justify-content: center;
      padding: 16px;
    }}

    .settings-overlay.active {{
      display: flex;
    }}

    .settings-card {{
      background: var(--bg-secondary);
      border: 1px solid var(--border-color);
      border-radius: var(--radius-lg);
      width: 100%;
      max-width: 650px;
      max-height: 90vh;
      overflow-y: auto;
      box-shadow: 0 20px 40px rgba(0, 0, 0, 0.6);
      display: flex;
      flex-direction: column;
    }}

    .settings-header {{
      padding: 20px 24px;
      border-bottom: 1px solid var(--border-color);
      display: flex;
      justify-content: space-between;
      align-items: center;
    }}

    .settings-title {{
      font-size: 1.25rem;
      font-weight: 800;
      color: var(--text-main);
      display: flex;
      align-items: center;
      gap: 8px;
    }}

    .close-btn {{
      background: transparent;
      border: none;
      color: var(--text-muted);
      font-size: 1.5rem;
      cursor: pointer;
      line-height: 1;
      padding: 4px 8px;
      border-radius: var(--radius-sm);
    }}

    .close-btn:hover {{
      color: var(--text-main);
      background: var(--bg-card);
    }}

    .settings-body {{
      padding: 24px;
      display: flex;
      flex-direction: column;
      gap: 24px;
    }}

    .setting-group {{
      display: flex;
      flex-direction: column;
      gap: 12px;
    }}

    .setting-label {{
      font-size: 0.95rem;
      font-weight: 700;
      color: var(--text-main);
      display: flex;
      justify-content: space-between;
      align-items: center;
    }}

    .setting-desc {{
      font-size: 0.8rem;
      color: var(--text-muted);
    }}

    .screen-slider-row {{
      background: var(--bg-card);
      border: 1px solid var(--border-subtle);
      border-radius: var(--radius-md);
      padding: 14px 18px;
      display: flex;
      flex-direction: column;
      gap: 10px;
    }}

    .screen-slider-top {{
      display: flex;
      justify-content: space-between;
      align-items: center;
    }}

    .screen-name {{
      font-weight: 700;
      font-size: 0.95rem;
      display: flex;
      align-items: center;
      gap: 8px;
    }}

    .screen-badge-size {{
      background: var(--bg-secondary);
      border: 1px solid var(--border-color);
      padding: 2px 10px;
      border-radius: 12px;
      font-family: var(--font-mono);
      font-size: 0.85rem;
      font-weight: 700;
      color: var(--accent-primary);
    }}

    .slider-container {{
      display: flex;
      align-items: center;
      gap: 14px;
    }}

    input[type="range"] {{
      flex: 1;
      height: 6px;
      border-radius: 3px;
      background: var(--border-color);
      outline: none;
      -webkit-appearance: none;
      cursor: pointer;
    }}

    input[type="range"]::-webkit-slider-thumb {{
      -webkit-appearance: none;
      width: 18px;
      height: 18px;
      border-radius: 50%;
      background: var(--accent-primary);
      cursor: pointer;
      box-shadow: 0 0 10px var(--accent-glow);
    }}

    .preset-pills {{
      display: flex;
      gap: 6px;
      flex-wrap: wrap;
    }}

    .preset-pill {{
      background: var(--bg-secondary);
      border: 1px solid var(--border-subtle);
      color: var(--text-muted);
      padding: 3px 8px;
      border-radius: 6px;
      font-size: 0.75rem;
      cursor: pointer;
    }}

    .preset-pill:hover {{
      border-color: var(--accent-primary);
      color: var(--text-main);
    }}

    .select-input {{
      width: 100%;
      padding: 10px 14px;
      border-radius: var(--radius-sm);
      background: var(--bg-card);
      border: 1px solid var(--border-color);
      color: var(--text-main);
      font-family: inherit;
      font-size: 0.95rem;
      cursor: pointer;
    }}

    .theme-grid {{
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 10px;
    }}

    .theme-btn {{
      padding: 10px;
      border-radius: var(--radius-md);
      border: 2px solid var(--border-color);
      background: var(--bg-card);
      color: var(--text-main);
      font-family: inherit;
      font-size: 0.88rem;
      font-weight: 700;
      cursor: pointer;
      text-align: center;
      transition: var(--transition-smooth);
    }}

    .theme-btn.active {{
      border-color: var(--accent-primary);
      background: rgba(56, 189, 248, 0.1);
    }}

    .settings-footer {{
      padding: 16px 24px;
      border-top: 1px solid var(--border-color);
      display: flex;
      justify-content: space-between;
      gap: 10px;
    }}

    /* ==========================================================================
       TELEPROMPTER AUTO-SCROLL CONTROLS (MINIMAL TOP-FIXED)
       ========================================================================== */
    [data-theme="teleprompter"] .site-header {{
      display: none !important;
    }}

    /* No buttons in the bottom of screen in teleprompter mode */
    [data-theme="teleprompter"] .floating-pill {{
      display: none !important;
    }}

    [data-theme="teleprompter"] .main-container {{
      padding-top: 65px;
    }}

    .teleprompter-controls {{
      display: none;
      position: fixed;
      top: 14px;
      left: 50%;
      transform: translateX(-50%);
      z-index: 9999;
      background: rgba(15, 15, 15, 0.88);
      border: 1px solid rgba(255, 255, 255, 0.16);
      padding: 4px 10px;
      border-radius: 999px;
      backdrop-filter: blur(16px);
      -webkit-backdrop-filter: blur(16px);
      box-shadow: 0 8px 24px rgba(0, 0, 0, 0.6), 0 2px 6px rgba(0, 0, 0, 0.4);
      align-items: center;
      gap: 6px;
      opacity: 0.92;
      transition: opacity 0.2s ease, transform 0.2s ease, box-shadow 0.2s ease;
      user-select: none;
      -webkit-user-select: none;
      direction: ltr;
    }}

    .teleprompter-controls:hover {{
      opacity: 1;
      box-shadow: 0 10px 28px rgba(0, 0, 0, 0.8), 0 0 18px rgba(250, 204, 21, 0.25);
    }}

    [data-theme="teleprompter"] .teleprompter-controls {{
      display: inline-flex;
    }}

    .prompter-btn {{
      width: 30px;
      height: 30px;
      border-radius: 50%;
      border: 1px solid transparent;
      background: rgba(255, 255, 255, 0.08);
      color: #f1f5f9;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      cursor: pointer;
      padding: 0;
      transition: all 0.15s ease;
      outline: none;
      flex-shrink: 0;
      position: relative;
    }}

    .prompter-btn:hover {{
      background: rgba(255, 255, 255, 0.2);
      color: #ffffff;
      transform: scale(1.08);
    }}

    .prompter-btn:active {{
      transform: scale(0.94);
    }}

    .prompter-play-btn {{
      background: #facc15;
      color: #0b0f19;
      border-color: #facc15;
    }}

    .prompter-play-btn:hover {{
      background: #fde047;
      color: #000;
      border-color: #fde047;
    }}

    .prompter-play-btn.is-playing {{
      background: #ef4444;
      color: #ffffff;
      border-color: #ef4444;
      box-shadow: 0 0 12px rgba(239, 68, 68, 0.5);
    }}

    .prompter-play-btn.is-playing:hover {{
      background: #dc2626;
      border-color: #dc2626;
    }}

    .prompter-speed-badge {{
      font-family: var(--font-mono);
      font-size: 0.8rem;
      font-weight: 700;
      color: #facc15;
      min-width: 42px;
      text-align: center;
      line-height: 1.4;
      padding: 3px 6px;
      border-radius: 6px;
      background: rgba(250, 204, 21, 0.12);
      letter-spacing: -0.2px;
      cursor: pointer;
      user-select: none;
      -webkit-user-select: none;
      transition: all 0.15s ease;
    }}

    .prompter-speed-badge:hover {{
      background: rgba(250, 204, 21, 0.25);
      color: #fde047;
      transform: scale(1.04);
    }}

    .prompter-font-btn {{
      font-size: 0.74rem;
      font-weight: 800;
      font-family: var(--font-mono);
      letter-spacing: -0.5px;
    }}

    .prompter-divider {{
      width: 1px;
      height: 16px;
      background: rgba(255, 255, 255, 0.18);
      margin: 0 2px;
    }}

    .prompter-exit-btn:hover {{
      background: rgba(239, 68, 68, 0.25);
      color: #fca5a5;
      border-color: rgba(239, 68, 68, 0.4);
    }}

    /* ==========================================================================
       MEDIA QUERIES FOR RESPONSIVE SCREENS & FONT SIZES
       ========================================================================== */
    /* 1. Mobile Screen (< 640px) */
    @media (max-width: 639px) {{
      html {{
        font-size: var(--fs-mobile);
      }}
      .site-header {{
        padding: 10px 12px;
      }}
      .header-content {{
        flex-direction: column;
        align-items: stretch;
        gap: 10px;
      }}
      .header-title-group {{
        gap: 8px;
      }}
      .header-logo {{
        width: 36px;
        height: 36px;
        font-size: 18px;
      }}
      .header-title {{
        font-size: 0.95rem;
      }}
      .header-subtitle {{
        font-size: 0.75rem;
      }}
      .header-actions {{
        display: grid;
        grid-template-columns: repeat(2, 1fr);
        gap: 6px;
        width: 100%;
      }}
      .header-actions .btn {{
        justify-content: center;
        padding: 6px 8px;
        font-size: 0.8rem;
      }}
      .main-container {{
        padding: 14px 10px;
      }}
      .script-hero-banner {{
        padding: 14px 12px;
        margin-bottom: 16px;
      }}
      .hero-title {{
        font-size: 1.15rem;
        word-break: break-word;
      }}
      .floating-pill {{
        bottom: 12px;
        right: 12px;
        padding: 4px 8px;
      }}
      .pill-btn {{
        padding: 4px 6px;
        font-size: 0.78rem;
      }}
      .teleprompter-controls {{
        top: 8px;
        padding: 3px 6px;
        gap: 4px;
        max-width: 98vw;
      }}
      .prompter-btn {{
        width: 28px;
        height: 28px;
      }}
      .prompter-speed-badge {{
        min-width: 36px;
        font-size: 0.72rem;
        padding: 2px 4px;
      }}
    }}

    /* 2. Tablet Screen (640px - 1023px) */
    @media (min-width: 640px) and (max-width: 1023px) {{
      html {{
        font-size: var(--fs-tablet);
      }}
      .main-container {{
        padding: 24px 20px;
      }}
    }}

    /* 3. Small Screen PC / Laptop (1024px - 1439px) */
    @media (min-width: 1024px) and (max-width: 1439px) {{
      html {{
        font-size: var(--fs-laptop);
      }}
      .main-container {{
        padding: 32px 28px;
      }}
    }}

    /* 4. Large Screen PC (>= 1440px) */
    @media (min-width: 1440px) {{
      html {{
        font-size: var(--fs-desktop);
      }}
      .main-container {{
        padding: 40px 36px;
      }}
    }}

    /* Simulator overrides when testing viewport sizes */
    body.sim-mobile {{
      font-size: var(--fs-mobile) !important;
    }}
    body.sim-mobile .main-container {{
      max-width: 400px !important;
      margin: 0 auto;
      box-shadow: 0 0 50px rgba(0,0,0,0.5);
    }}
    body.sim-tablet {{
      font-size: var(--fs-tablet) !important;
    }}
    body.sim-tablet .main-container {{
      max-width: 768px !important;
      margin: 0 auto;
      box-shadow: 0 0 50px rgba(0,0,0,0.5);
    }}
    body.sim-laptop {{
      font-size: var(--fs-laptop) !important;
    }}
    body.sim-laptop .main-container {{
      max-width: 1100px !important;
      margin: 0 auto;
    }}
    body.sim-desktop {{
      font-size: var(--fs-desktop) !important;
    }}
    body.sim-desktop .main-container {{
      max-width: 1400px !important;
      margin: 0 auto;
    }}

    /* ==========================================================================
       PRINT & PDF STYLES
       ========================================================================== */
    @media print {{
      @page {{
        size: A4;
        margin: 14mm 12mm 14mm 12mm;
      }}

      * {{
        -webkit-print-color-adjust: exact !important;
        print-color-adjust: exact !important;
      }}

      body {{
        background: #ffffff !important;
        color: #0f172a !important;
        font-size: 13.5px !important;
        line-height: 1.6 !important;
      }}

      .site-wrapper {{
        min-height: auto !important;
        display: block !important;
      }}

      .no-print,
      .site-header,
      .floating-pill,
      .settings-overlay,
      .teleprompter-bar,
      .teleprompter-controls,
      .copy-button {{
        display: none !important;
      }}

      .main-container {{
        max-width: 100% !important;
        width: 100% !important;
        padding: 0 !important;
        margin: 0 !important;
      }}

      .script-hero-banner {{
        background: #f0f9ff !important;
        border: 1px solid #bae6fd !important;
        box-shadow: none !important;
        margin-bottom: 20px !important;
        padding: 16px 20px !important;
      }}

      .hero-badge {{
        background: #e0f2fe !important;
        color: #0369a1 !important;
      }}

      .hero-title {{
        color: #0369a1 !important;
      }}

      .table-responsive {{
        background: #ffffff !important;
        border: 1px solid #cbd5e1 !important;
        box-shadow: none !important;
        margin: 1em 0 !important;
      }}

      table {{
        color: #0f172a !important;
      }}

      table th {{
        background: #f1f5f9 !important;
        color: #0369a1 !important;
        border-bottom: 2px solid #cbd5e1 !important;
      }}

      table td {{
        color: #1e293b !important;
        border-bottom: 1px solid #e2e8f0 !important;
      }}

      tbody tr:hover {{
        background: transparent !important;
      }}

      .code-card {{
        background: #f8fafc !important;
        border: 1px solid #cbd5e1 !important;
        box-shadow: none !important;
        break-inside: avoid;
        page-break-inside: avoid;
      }}

      .code-card-header {{
        background: #f1f5f9 !important;
        border-bottom: 1px solid #cbd5e1 !important;
      }}

      .code-tag {{
        color: #475569 !important;
      }}

      pre {{
        background: #f8fafc !important;
        color: #0f172a !important;
        border: none !important;
      }}

      code {{
        color: #0f172a !important;
      }}

      .speaker-tag {{
        background: #e0f2fe !important;
        color: #0369a1 !important;
        border: 1px solid #7dd3fc !important;
      }}

      .director-cue {{
        background: #fefce8 !important;
        color: #854d0e !important;
        border-right: 4px solid #eab308 !important;
        break-inside: avoid;
        page-break-inside: avoid;
      }}

      tr {{
        break-inside: avoid;
        page-break-inside: avoid;
      }}

      h1, h2, h3 {{
        break-after: avoid;
        page-break-after: avoid;
      }}

      h1 {{
        color: #0284c7 !important;
        border-bottom: 2px solid #0284c7 !important;
      }}

      h2 {{
        color: #0f172a !important;
        border-right-color: #0284c7 !important;
        background: #f0f9ff !important;
        margin-top: 22px !important;
      }}

      h3 {{
        color: #0369a1 !important;
      }}

      h4 {{
        color: #334155 !important;
      }}

      strong {{
        color: #0f172a !important;
      }}

      p {{
        color: #1e293b !important;
      }}
    }}
  </style>
</head>
<body>

  <div class="site-wrapper">
    <!-- Top Header -->
    <header class="site-header no-print">
      <div class="header-content">
        <div class="header-title-group">
          <div class="header-logo">🎬</div>
          <div>
            <div class="header-title">{title}</div>
            <div class="header-subtitle">حزمة السكريبت المعتمدة • جاهزة للإنتاج والتصوير</div>
          </div>
        </div>
        
        <div class="header-actions">
          <button class="btn" onclick="toggleTheme()" title="تغيير المظهر">
            <span id="theme-icon">☀️</span> <span id="theme-text">نهاري</span>
          </button>
          <button class="btn" onclick="setTeleprompterMode()" title="وضع ملقن التصوير">
            <span>🎥</span> <span>وضع الملقن</span>
          </button>
          <button class="btn" onclick="openSettings()" title="التحكم بأحجام الخطوط والشاشات">
            <span>🔤</span> <span>الخط والشاشات</span>
          </button>
          <button class="btn btn-primary" onclick="window.print()" title="طباعة أو تصدير PDF">
            <span>🖨️</span> <span>طباعة / PDF</span>
          </button>
        </div>
      </div>
    </header>

    <!-- Main Content -->
    <main class="main-container">
      
      <!-- Teleprompter Minimal Fixed Control Bar (Active only in teleprompter mode) -->
      <div class="teleprompter-controls no-print" id="teleprompterControls" role="toolbar" aria-label="أدوات التحكم بالملقن">
        <!-- Play / Pause -->
        <button type="button" class="prompter-btn prompter-play-btn" id="teleprompterPlayPauseBtn" onclick="toggleAutoScroll(event)" title="تشغيل / إيقاف التمرير التلقائي (Space)" aria-label="تشغيل أو إيقاف التمرير">
          <svg class="icon-play" width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>
          <svg class="icon-pause" width="13" height="13" viewBox="0 0 24 24" fill="currentColor" style="display:none;"><path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z"/></svg>
        </button>

        <!-- Speed Down (Micro-step 0.1x) -->
        <button type="button" class="prompter-btn" id="teleprompterSpeedDownBtn"
                onmousedown="startSpeedRepeat(-0.1, event)" onmouseup="stopSpeedRepeat()" onmouseleave="stopSpeedRepeat()"
                ontouchstart="startSpeedRepeat(-0.1, event)" ontouchend="stopSpeedRepeat()"
                onclick="handleSpeedClick(-0.1, event)"
                title="إبطاء السرعة (ArrowDown) [خطوة 0.1x / Shift: 0.5x]" aria-label="إبطاء السرعة">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"><line x1="5" y1="12" x2="19" y2="12"/></svg>
        </button>

        <!-- Speed Value Badge -->
        <span class="prompter-speed-badge" id="teleprompterSpeedVal" onclick="cycleSpeedPreset(event)" title="سرعة التمرير الحالية (انقر للتبديل السريع بين السرعات)">0.5x</span>

        <!-- Speed Up (Micro-step 0.1x) -->
        <button type="button" class="prompter-btn" id="teleprompterSpeedUpBtn"
                onmousedown="startSpeedRepeat(0.1, event)" onmouseup="stopSpeedRepeat()" onmouseleave="stopSpeedRepeat()"
                ontouchstart="startSpeedRepeat(0.1, event)" ontouchend="stopSpeedRepeat()"
                onclick="handleSpeedClick(0.1, event)"
                title="تسريع السرعة (ArrowUp) [خطوة 0.1x / Shift: 0.5x]" aria-label="تسريع السرعة">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
        </button>

        <span class="prompter-divider"></span>

        <!-- Rewind / Scroll to Top -->
        <button type="button" class="prompter-btn" id="teleprompterRestartBtn" onclick="restartPrompter(event)" title="العودة لأول السكريبت (Home)" aria-label="العودة للبداية">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polygon points="19 20 9 12 19 4 19 20"></polygon><line x1="5" y1="19" x2="5" y2="5"></line></svg>
        </button>

        <span class="prompter-divider"></span>

        <!-- Font Size Controls (At top of screen) -->
        <button type="button" class="prompter-btn prompter-font-btn" id="teleprompterFontDownBtn" onclick="adjustCurrentScreenFont(-1, event)" title="تصغير حجم الخط" aria-label="تصغير الخط">
          <span>A-</span>
        </button>
        <button type="button" class="prompter-btn prompter-font-btn" id="teleprompterFontUpBtn" onclick="adjustCurrentScreenFont(1, event)" title="تكبير حجم الخط" aria-label="تكبير الخط">
          <span>A+</span>
        </button>

        <!-- Screen / Font Settings Modal Button -->
        <button type="button" class="prompter-btn" id="teleprompterSettingsBtn" onclick="openSettings(event)" title="إعدادات الشاشات والخطوط" aria-label="إعدادات الشاشات والخطوط">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"></circle><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"></path></svg>
        </button>

        <span class="prompter-divider"></span>

        <!-- Exit Teleprompter Mode -->
        <button type="button" class="prompter-btn prompter-exit-btn" id="teleprompterExitBtn" onclick="exitTeleprompterMode(event)" title="خروج من وضع الملقن (Esc)" aria-label="خروج من وضع الملقن">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
        </button>
      </div>

      <!-- Markdown Rendered Body -->
      <article class="script-body" id="scriptContent">
        <div class="script-hero-banner">
          <div class="hero-badge">🎬 حزمة السكريبت المعتمدة • جاهزة للإنتاج والتصوير</div>
          <h1 class="hero-title">{title}</h1>
        </div>
        {body_content}
      </article>

    </main>
  </div>

  <!-- Quick Floating Action Pill -->
  <div class="floating-pill no-print">
    <button class="pill-btn" onclick="adjustCurrentScreenFont(-1)" title="تصغير الخط">
      <span>A-</span>
    </button>
    <button class="pill-btn" onclick="adjustCurrentScreenFont(1)" title="تكبير الخط">
      <span>A+</span>
    </button>
    <button class="pill-btn pill-btn-primary" onclick="openSettings()" title="إعدادات حجم الخط لكل الشاشات">
      <span>⚙️</span> <span>التحكم بالشاشات</span>
    </button>
  </div>

  <!-- Settings Modal (Control font size of Mobile, Tablet, Small PC, Large PC) -->
  <div class="settings-overlay no-print" id="settingsModal" onclick="handleOverlayClick(event)">
    <div class="settings-card">
      <div class="settings-header">
        <div class="settings-title">
          <span>🎛️</span>
          <span>التحكم في أحجام الخطوط والشاشات</span>
        </div>
        <button class="close-btn" onclick="closeSettings()">&times;</button>
      </div>

      <div class="settings-body">
        
        <!-- Current Screen Badge -->
        <div style="background: rgba(56, 189, 248, 0.1); border: 1px solid var(--accent-primary); border-radius: var(--radius-sm); padding: 10px 14px; font-size: 0.9rem; color: var(--accent-primary); display: flex; align-items: center; justify-content: space-between;">
          <span>🖥️ الشاشة النشطة حالياً:</span>
          <strong id="currentScreenLabel">شاشة لابتوب</strong>
        </div>

        <!-- 1. Mobile Font Size Control -->
        <div class="screen-slider-row">
          <div class="screen-slider-top">
            <span class="screen-name">📱 شاشة الموبايل (أقل من 640px)</span>
            <span class="screen-badge-size" id="badge-fs-mobile">16px</span>
          </div>
          <div class="slider-container">
            <input type="range" id="range-fs-mobile" min="12" max="28" value="16" oninput="updateFontSize('mobile', this.value)">
          </div>
          <div class="preset-pills">
            <span class="preset-pill" onclick="updateFontSize('mobile', 14)">صغير (14px)</span>
            <span class="preset-pill" onclick="updateFontSize('mobile', 16)">افتراضي (16px)</span>
            <span class="preset-pill" onclick="updateFontSize('mobile', 18)">كبير (18px)</span>
            <span class="preset-pill" onclick="updateFontSize('mobile', 22)">كبير جداً (22px)</span>
          </div>
        </div>

        <!-- 2. Tablet Font Size Control -->
        <div class="screen-slider-row">
          <div class="screen-slider-top">
            <span class="screen-name">📱 شاشة التابلت (640px - 1023px)</span>
            <span class="screen-badge-size" id="badge-fs-tablet">18px</span>
          </div>
          <div class="slider-container">
            <input type="range" id="range-fs-tablet" min="14" max="32" value="18" oninput="updateFontSize('tablet', this.value)">
          </div>
          <div class="preset-pills">
            <span class="preset-pill" onclick="updateFontSize('tablet', 16)">صغير (16px)</span>
            <span class="preset-pill" onclick="updateFontSize('tablet', 18)">افتراضي (18px)</span>
            <span class="preset-pill" onclick="updateFontSize('tablet', 22)">كبير (22px)</span>
            <span class="preset-pill" onclick="updateFontSize('tablet', 26)">كبير جداً (26px)</span>
          </div>
        </div>

        <!-- 3. Small Screen PC / Laptop Font Size Control -->
        <div class="screen-slider-row">
          <div class="screen-slider-top">
            <span class="screen-name">💻 كمبيوتر صغير ولابتوب (1024px - 1439px)</span>
            <span class="screen-badge-size" id="badge-fs-laptop">20px</span>
          </div>
          <div class="slider-container">
            <input type="range" id="range-fs-laptop" min="14" max="36" value="20" oninput="updateFontSize('laptop', this.value)">
          </div>
          <div class="preset-pills">
            <span class="preset-pill" onclick="updateFontSize('laptop', 18)">صغير (18px)</span>
            <span class="preset-pill" onclick="updateFontSize('laptop', 20)">افتراضي (20px)</span>
            <span class="preset-pill" onclick="updateFontSize('laptop', 24)">كبير (24px)</span>
            <span class="preset-pill" onclick="updateFontSize('laptop', 28)">كبير جداً (28px)</span>
          </div>
        </div>

        <!-- 4. Large Screen PC Font Size Control -->
        <div class="screen-slider-row">
          <div class="screen-slider-top">
            <span class="screen-name">🖥️ كمبيوتر وشاشات كبيرة (1440px فأكثر)</span>
            <span class="screen-badge-size" id="badge-fs-desktop">22px</span>
          </div>
          <div class="slider-container">
            <input type="range" id="range-fs-desktop" min="16" max="44" value="22" oninput="updateFontSize('desktop', this.value)">
          </div>
          <div class="preset-pills">
            <span class="preset-pill" onclick="updateFontSize('desktop', 20)">صغير (20px)</span>
            <span class="preset-pill" onclick="updateFontSize('desktop', 22)">افتراضي (22px)</span>
            <span class="preset-pill" onclick="updateFontSize('desktop', 26)">كبير (26px)</span>
            <span class="preset-pill" onclick="updateFontSize('desktop', 32)">كبير جداً (32px)</span>
          </div>
        </div>

        <!-- Viewport Simulator for PC Users -->
        <div class="setting-group">
          <div class="setting-label">
            <span>📐 معاينة محاكاة الشاشات (Viewport Simulator)</span>
          </div>
          <div class="setting-desc">يمكنك تجربة مظهر الشاشات المختلفة وأحجام خطوطها مباشرة من هنا:</div>
          <select class="select-input" id="simSelect" onchange="changeSimulation(this.value)">
            <option value="real">عرض الشاشة الطبيعي (الحالي)</option>
            <option value="mobile">📱 محاكاة الموبايل (390px)</option>
            <option value="tablet">📱 محاكاة التابلت (768px)</option>
            <option value="laptop">💻 محاكاة اللابتوب (1100px)</option>
            <option value="desktop">🖥️ محاكاة الشاشة الكبيرة (1440px+)</option>
          </select>
        </div>

        <!-- Font Family Selection -->
        <div class="setting-group">
          <div class="setting-label">
            <span>🖋️ نوع الخط (Font Family)</span>
          </div>
          <select class="select-input" id="fontFamilySelect" onchange="changeFontFamily(this.value)">
            <option value="'Cairo', sans-serif">Cairo (عصري، واضح وسهل القراءة - افتراضي)</option>
            <option value="'Tajawal', sans-serif">Tajawal (انسيابي ومريح للعين)</option>
            <option value="'Amiri', serif">Amiri (نسخي أدبي تقليدي)</option>
            <option value="system-ui, -apple-system, BlinkMacSystemFont, sans-serif">System UI (خط النظام الافتراضي)</option>
          </select>
        </div>

        <!-- Line Height -->
        <div class="setting-group">
          <div class="setting-label">
            <span>↕️ تباعد الأسطر (Line Height)</span>
            <span id="badge-line-height" style="color: var(--accent-primary); font-family: var(--font-mono);">1.8</span>
          </div>
          <input type="range" id="range-line-height" min="14" max="24" value="18" oninput="updateLineHeight(this.value / 10)">
        </div>

        <!-- Themes -->
        <div class="setting-group">
          <div class="setting-label">
            <span>🎨 نمط المظهر والقراءة</span>
          </div>
          <div class="theme-grid">
            <button class="theme-btn" id="btn-theme-dark" onclick="applyTheme('dark')">🌙 ليلي</button>
            <button class="theme-btn" id="btn-theme-light" onclick="applyTheme('light')">☀️ نهاري</button>
            <button class="theme-btn" id="btn-theme-tele" onclick="applyTheme('teleprompter')">🎬 ملقن</button>
          </div>
        </div>

      </div>

      <div class="settings-footer">
        <button class="btn" onclick="resetToDefaults()">🔄 استعادة الافتراضي</button>
        <button class="btn btn-primary" onclick="closeSettings()">حفظ وإغلاق</button>
      </div>
    </div>
  </div>

  <!-- Interactive JavaScript Logic -->
  <script>
    // Configuration state
    const DEFAULT_CONFIG = {{
      fsMobile: 16,
      fsTablet: 18,
      fsLaptop: 20,
      fsDesktop: 22,
      fontFamily: "'Cairo', sans-serif",
      lineHeight: 1.8,
      theme: 'dark'
    }};

    let appConfig = {{ ...DEFAULT_CONFIG }};

    // Load saved settings from localStorage
    function loadSavedSettings() {{
      try {{
        const saved = localStorage.getItem('script_viewer_settings');
        if (saved) {{
          const parsed = JSON.parse(saved);
          appConfig = {{ ...appConfig, ...parsed }};
        }}
      }} catch (e) {{
        console.warn('Could not read localStorage:', e);
      }}
      applyAllSettings();
    }}

    // Save to localStorage
    function persistSettings() {{
      try {{
        localStorage.setItem('script_viewer_settings', JSON.stringify(appConfig));
      }} catch (e) {{}}
    }}

    // Apply font size for a specific screen tier
    function updateFontSize(tier, val) {{
      val = parseInt(val, 10);
      const root = document.documentElement;
      
      if (tier === 'mobile') {{
        appConfig.fsMobile = val;
        root.style.setProperty('--fs-mobile', val + 'px');
        document.getElementById('badge-fs-mobile').textContent = val + 'px';
        document.getElementById('range-fs-mobile').value = val;
      }} else if (tier === 'tablet') {{
        appConfig.fsTablet = val;
        root.style.setProperty('--fs-tablet', val + 'px');
        document.getElementById('badge-fs-tablet').textContent = val + 'px';
        document.getElementById('range-fs-tablet').value = val;
      }} else if (tier === 'laptop') {{
        appConfig.fsLaptop = val;
        root.style.setProperty('--fs-laptop', val + 'px');
        document.getElementById('badge-fs-laptop').textContent = val + 'px';
        document.getElementById('range-fs-laptop').value = val;
      }} else if (tier === 'desktop') {{
        appConfig.fsDesktop = val;
        root.style.setProperty('--fs-desktop', val + 'px');
        document.getElementById('badge-fs-desktop').textContent = val + 'px';
        document.getElementById('range-fs-desktop').value = val;
      }}
      persistSettings();
    }}

    // Quick font size adjustment for current screen
    function adjustCurrentScreenFont(delta, e) {{
      if (e) {{
        e.preventDefault();
        e.stopPropagation();
      }}
      const width = window.innerWidth;
      if (width < 640) {{
        updateFontSize('mobile', appConfig.fsMobile + delta);
      }} else if (width < 1024) {{
        updateFontSize('tablet', appConfig.fsTablet + delta);
      }} else if (width < 1440) {{
        updateFontSize('laptop', appConfig.fsLaptop + delta);
      }} else {{
        updateFontSize('desktop', appConfig.fsDesktop + delta);
      }}
    }}

    // Update line height
    function updateLineHeight(val) {{
      appConfig.lineHeight = val;
      document.documentElement.style.setProperty('--line-height', val);
      document.getElementById('badge-line-height').textContent = val.toFixed(1);
      document.getElementById('range-line-height').value = Math.round(val * 10);
      persistSettings();
    }}

    // Change font family
    function changeFontFamily(family) {{
      appConfig.fontFamily = family;
      document.documentElement.style.setProperty('--font-family', family);
      document.getElementById('fontFamilySelect').value = family;
      persistSettings();
    }}

    // Change Theme
    function applyTheme(theme) {{
      appConfig.theme = theme;
      document.documentElement.setAttribute('data-theme', theme);
      
      const icon = document.getElementById('theme-icon');
      const text = document.getElementById('theme-text');
      
      if (theme === 'light') {{
        if (icon) icon.textContent = '🌙';
        if (text) text.textContent = 'ليلي';
      }} else if (theme === 'dark') {{
        if (icon) icon.textContent = '☀️';
        if (text) text.textContent = 'نهاري';
      }} else if (theme === 'teleprompter') {{
        if (icon) icon.textContent = '🎬';
        if (text) text.textContent = 'ملقن';
      }}

      // Update buttons active class
      document.querySelectorAll('.theme-btn').forEach(btn => btn.classList.remove('active'));
      const activeBtn = document.getElementById('btn-theme-' + (theme === 'teleprompter' ? 'tele' : theme));
      if (activeBtn) activeBtn.classList.add('active');

      persistSettings();
    }}

    function toggleTheme() {{
      const current = document.documentElement.getAttribute('data-theme') || 'dark';
      if (current === 'dark') {{
        applyTheme('light');
      }} else {{
        applyTheme('dark');
      }}
    }}

    let previousThemeBeforeTeleprompter = 'dark';

    function setTeleprompterMode() {{
      const current = document.documentElement.getAttribute('data-theme') || 'dark';
      if (current !== 'teleprompter') {{
        previousThemeBeforeTeleprompter = current;
      }}
      applyTheme('teleprompter');
      updatePrompterUI();
    }}

    function exitTeleprompterMode(e) {{
      if (e) {{
        e.preventDefault();
        e.stopPropagation();
      }}
      stopAutoScroll();
      applyTheme(previousThemeBeforeTeleprompter || 'dark');
    }}

    // Simulation selector
    function changeSimulation(val) {{
      document.body.className = '';
      if (val !== 'real') {{
        document.body.classList.add('sim-' + val);
      }}
    }}

    // Reset to defaults
    function resetToDefaults() {{
      appConfig = {{ ...DEFAULT_CONFIG }};
      localStorage.removeItem('script_viewer_settings');
      applyAllSettings();
    }}

    // Apply all settings to DOM
    function applyAllSettings() {{
      updateFontSize('mobile', appConfig.fsMobile);
      updateFontSize('tablet', appConfig.fsTablet);
      updateFontSize('laptop', appConfig.fsLaptop);
      updateFontSize('desktop', appConfig.fsDesktop);
      updateLineHeight(appConfig.lineHeight);
      changeFontFamily(appConfig.fontFamily);
      applyTheme(appConfig.theme);
      detectActiveScreen();
    }}

    // Detect active screen
    function detectActiveScreen() {{
      const width = window.innerWidth;
      const label = document.getElementById('currentScreenLabel');
      if (!label) return;
      if (width < 640) {{
        label.textContent = '📱 شاشة موبايل (' + width + 'px)';
      }} else if (width < 1024) {{
        label.textContent = '📱 شاشة تابلت (' + width + 'px)';
      }} else if (width < 1440) {{
        label.textContent = '💻 شاشة لابتوب / كمبيوتر صغير (' + width + 'px)';
      }} else {{
        label.textContent = '🖥️ شاشة كمبيوتر كبيرة (' + width + 'px)';
      }}
    }}

    window.addEventListener('resize', detectActiveScreen);

    // Modal controls
    function openSettings(e) {{
      if (e) {{
        e.preventDefault();
        e.stopPropagation();
      }}
      detectActiveScreen();
      document.getElementById('settingsModal').classList.add('active');
    }}

    function closeSettings() {{
      document.getElementById('settingsModal').classList.remove('active');
    }}

    function handleOverlayClick(e) {{
      if (e.target === document.getElementById('settingsModal')) {{
        closeSettings();
      }}
    }}

    // Copy to clipboard helper
    function copyCodeBlock(button) {{
      const card = button.closest('.code-card');
      const code = card.querySelector('code');
      if (!code) return;
      
      navigator.clipboard.writeText(code.innerText).then(() => {{
        button.classList.add('copied');
        const originalText = button.innerHTML;
        button.innerHTML = '<span class="btn-icon">✓</span> <span class="btn-text">تم النسخ!</span>';
        setTimeout(() => {{
          button.innerHTML = originalText;
          button.classList.remove('copied');
        }}, 2000);
      }}).catch(err => {{
        alert('تعذر النسخ التلقائي: ' + err);
      }});
    }}

    // Auto-Scroll Feature for Teleprompter
    let isScrolling = false;
    let scrollSpeed = 0.5; // Very low starting speed
    const MIN_SPEED = 0.1;
    const MAX_SPEED = 5.0;
    const DEFAULT_SPEED = 0.5;
    const SPEED_STEP = 0.1;
    let prompterAnimationFrame = null;
    let lastScrollTimestamp = null;
    let fractionalScrollAccumulator = 0;

    let speedRepeatTimer = null;
    let speedRepeatInterval = null;
    let speedAdjustedOnMouseDown = false;

    function updatePrompterUI() {{
      const playPauseBtn = document.getElementById('teleprompterPlayPauseBtn');
      const speedVal = document.getElementById('teleprompterSpeedVal');
      const speedDownBtn = document.getElementById('teleprompterSpeedDownBtn');
      const speedUpBtn = document.getElementById('teleprompterSpeedUpBtn');

      if (playPauseBtn) {{
        const playIcon = playPauseBtn.querySelector('.icon-play');
        const pauseIcon = playPauseBtn.querySelector('.icon-pause');
        if (isScrolling) {{
          playPauseBtn.classList.add('is-playing');
          playPauseBtn.setAttribute('title', 'إيقاف مؤقت للتمرير (Space)');
          playPauseBtn.setAttribute('aria-label', 'إيقاف مؤقت للتمرير');
          if (playIcon) playIcon.style.display = 'none';
          if (pauseIcon) pauseIcon.style.display = 'block';
        }} else {{
          playPauseBtn.classList.remove('is-playing');
          playPauseBtn.setAttribute('title', 'تشغيل التمرير التلقائي (Space)');
          playPauseBtn.setAttribute('aria-label', 'تشغيل التمرير التلقائي');
          if (playIcon) playIcon.style.display = 'block';
          if (pauseIcon) pauseIcon.style.display = 'none';
        }}
      }}

      if (speedVal) {{
        speedVal.textContent = scrollSpeed.toFixed(1) + 'x';
      }}

      if (speedDownBtn) {{
        const isMin = scrollSpeed <= MIN_SPEED + 0.001;
        speedDownBtn.style.opacity = isMin ? '0.35' : '1';
        speedDownBtn.style.pointerEvents = isMin ? 'none' : 'auto';
      }}

      if (speedUpBtn) {{
        const isMax = scrollSpeed >= MAX_SPEED - 0.001;
        speedUpBtn.style.opacity = isMax ? '0.35' : '1';
        speedUpBtn.style.pointerEvents = isMax ? 'none' : 'auto';
      }}
    }}

    function toggleAutoScroll(e) {{
      if (e) {{
        e.preventDefault();
        e.stopPropagation();
      }}
      if (isScrolling) {{
        stopAutoScroll();
      }} else {{
        startAutoScroll();
      }}
    }}

    function autoScrollLoop(timestamp) {{
      if (!isScrolling) return;
      if (!lastScrollTimestamp) lastScrollTimestamp = timestamp;
      const dt = (timestamp - lastScrollTimestamp) / 1000;
      lastScrollTimestamp = timestamp;

      // Avoid jumping if frame lagged or tab switched
      if (dt > 0.1) {{
        prompterAnimationFrame = requestAnimationFrame(autoScrollLoop);
        return;
      }}

      // Base speed: 25 px/sec at 1.0x.
      // At 0.1x = 2.5 px/sec (ultra slow, ~17s per line)
      // At 0.5x = 12.5 px/sec (relaxed natural reading pace, ~3.4s per line)
      // At 1.0x = 25 px/sec (standard speaking pace)
      // At 2.0x = 50 px/sec (fast reading)
      const BASE_PX_PER_SEC = 25;
      const pxPerSec = scrollSpeed * BASE_PX_PER_SEC;
      fractionalScrollAccumulator += pxPerSec * dt;

      if (fractionalScrollAccumulator >= 1) {{
        const step = Math.floor(fractionalScrollAccumulator);
        window.scrollBy(0, step);
        fractionalScrollAccumulator -= step;
      }}

      // Stop gracefully if reached bottom
      const reachedBottom = (window.innerHeight + window.scrollY) >= (document.documentElement.scrollHeight - 4);
      if (reachedBottom) {{
        stopAutoScroll();
        return;
      }}

      prompterAnimationFrame = requestAnimationFrame(autoScrollLoop);
    }}

    function startAutoScroll() {{
      if (isScrolling) return;
      isScrolling = true;
      lastScrollTimestamp = null;
      fractionalScrollAccumulator = 0;
      updatePrompterUI();
      prompterAnimationFrame = requestAnimationFrame(autoScrollLoop);
    }}

    function stopAutoScroll() {{
      isScrolling = false;
      if (prompterAnimationFrame) {{
        cancelAnimationFrame(prompterAnimationFrame);
        prompterAnimationFrame = null;
      }}
      lastScrollTimestamp = null;
      fractionalScrollAccumulator = 0;
      updatePrompterUI();
    }}

    function restartPrompter(e) {{
      if (e) {{
        e.preventDefault();
        e.stopPropagation();
      }}
      stopAutoScroll();
      window.scrollTo({{ top: 0, behavior: 'smooth' }});
    }}

    function adjustScrollSpeed(delta, e) {{
      if (e) {{
        e.preventDefault();
        e.stopPropagation();
      }}
      let stepDelta = delta;
      if (e && e.shiftKey) {{
        stepDelta = delta > 0 ? 0.5 : -0.5;
      }}
      let newSpeed = Math.round((scrollSpeed + stepDelta) * 10) / 10;
      newSpeed = Math.max(MIN_SPEED, Math.min(MAX_SPEED, newSpeed));
      if (Math.abs(newSpeed - scrollSpeed) > 0.001) {{
        scrollSpeed = newSpeed;
        try {{
          localStorage.setItem('script_prompter_speed_v2', scrollSpeed.toString());
        }} catch (err) {{}}
        updatePrompterUI();
      }}
    }}

    function startSpeedRepeat(delta, e) {{
      if (e) {{
        e.preventDefault();
        e.stopPropagation();
      }}
      speedAdjustedOnMouseDown = true;
      adjustScrollSpeed(delta, e);
      stopSpeedRepeat();
      speedRepeatTimer = setTimeout(() => {{
        speedRepeatInterval = setInterval(() => {{
          adjustScrollSpeed(delta, e);
        }}, 110);
      }}, 350);
    }}

    function stopSpeedRepeat() {{
      if (speedRepeatTimer) {{
        clearTimeout(speedRepeatTimer);
        speedRepeatTimer = null;
      }}
      if (speedRepeatInterval) {{
        clearInterval(speedRepeatInterval);
        speedRepeatInterval = null;
      }}
    }}

    function handleSpeedClick(delta, e) {{
      if (e) {{
        e.preventDefault();
        e.stopPropagation();
      }}
      if (!speedAdjustedOnMouseDown) {{
        adjustScrollSpeed(delta, e);
      }}
      speedAdjustedOnMouseDown = false;
    }}

    const SPEED_PRESETS = [0.2, 0.5, 0.8, 1.0, 1.5, 2.0];
    function cycleSpeedPreset(e) {{
      if (e) {{
        e.preventDefault();
        e.stopPropagation();
      }}
      let nextSpeed = SPEED_PRESETS[0];
      for (let i = 0; i < SPEED_PRESETS.length; i++) {{
        if (scrollSpeed < SPEED_PRESETS[i] - 0.05) {{
          nextSpeed = SPEED_PRESETS[i];
          break;
        }}
      }}
      scrollSpeed = nextSpeed;
      try {{
        localStorage.setItem('script_prompter_speed_v2', scrollSpeed.toString());
      }} catch (err) {{}}
      updatePrompterUI();
    }}

    // Keyboard shortcuts for teleprompter mode
    document.addEventListener('keydown', (e) => {{
      const theme = document.documentElement.getAttribute('data-theme');
      if (theme !== 'teleprompter') return;

      if (['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName)) return;

      if (e.code === 'Space') {{
        e.preventDefault();
        toggleAutoScroll();
      }} else if (e.code === 'ArrowDown' || e.key === '-' || e.key === '_') {{
        e.preventDefault();
        adjustScrollSpeed(e.shiftKey ? -0.5 : -SPEED_STEP, e);
      }} else if (e.code === 'ArrowUp' || e.key === '+' || e.key === '=') {{
        e.preventDefault();
        adjustScrollSpeed(e.shiftKey ? 0.5 : SPEED_STEP, e);
      }} else if (e.code === 'Home') {{
        e.preventDefault();
        restartPrompter(e);
      }} else if (e.code === 'Escape') {{
        e.preventDefault();
        exitTeleprompterMode();
      }}
    }});

    // Initialize on page load
    document.addEventListener('DOMContentLoaded', () => {{
      loadSavedSettings();
      try {{
        const savedSpeed = localStorage.getItem('script_prompter_speed_v2');
        if (savedSpeed) {{
          const p = parseFloat(savedSpeed);
          if (!isNaN(p) && p >= MIN_SPEED && p <= MAX_SPEED) {{
            scrollSpeed = Math.round(p * 10) / 10;
          }}
        }}
      }} catch (err) {{}}
      updatePrompterUI();
    }});
  </script>
</body>
</html>
"""
    return html_template


# ==============================================================================
# MICROSOFT WORD (.DOCX) OPENXML GENERATION ENGINE
# Native zero-dependency builder with native Arabic RTL and script styling
# ==============================================================================

def escape_xml(text):
    """Safely escape text for XML inclusion."""
    if not text:
        return ""
    return saxutils.escape(str(text))


def parse_inline_markdown(text):
    """
    Parse inline markdown (bold, italic, code) into a list of tuples:
    (content, is_bold, is_italic, is_code)
    """
    if not text:
        return []

    tokens = []
    pattern = re.compile(
        r'(\*\*\*(.+?)\*\*\*|___(.+?)___|'
        r'\*\*(.+?)\*\*|__(.+?)__|'
        r'\*(.+?)\*|_(.+?)_|'
        r'`([^`]+)`)'
    )

    last_idx = 0
    for m in pattern.finditer(text):
        start, end = m.span()
        if start > last_idx:
            raw_chunk = text[last_idx:start]
            tokens.append((raw_chunk, False, False, False))

        full_match = m.group(0)
        if full_match.startswith('***') or full_match.startswith('___'):
            content = m.group(2) or m.group(3)
            tokens.append((content, True, True, False))
        elif full_match.startswith('**') or full_match.startswith('__'):
            content = m.group(4) or m.group(5)
            tokens.append((content, True, False, False))
        elif full_match.startswith('*') or full_match.startswith('_'):
            content = m.group(6) or m.group(7)
            tokens.append((content, False, True, False))
        elif full_match.startswith('`'):
            content = m.group(8)
            tokens.append((content, False, False, True))

        last_idx = end

    if last_idx < len(text):
        tokens.append((text[last_idx:], False, False, False))

    return tokens


def build_run_xml(text, is_bold=False, is_italic=False, is_code=False, font_size=24, color=None, is_rtl=True, font_name=None):
    """Build OpenXML <w:r> run with typography and RTL flags."""
    escaped = escape_xml(text)
    if not font_name:
        font_name = "Consolas" if is_code else "Cairo"

    rpr_items = []
    rpr_items.append(f'<w:rFonts w:ascii="{font_name}" w:hAnsi="{font_name}" w:cs="{font_name}"/>')
    if is_bold:
        rpr_items.append('<w:b/><w:bCs/>')
    if is_italic:
        rpr_items.append('<w:i/><w:iCs/>')
    if font_size:
        rpr_items.append(f'<w:sz w:val="{font_size}"/><w:szCs w:val="{font_size}"/>')
    if color:
        rpr_items.append(f'<w:color w:val="{color}"/>')
    if is_rtl:
        rpr_items.append('<w:rtl w:val="1"/>')

    rpr_str = f"<w:rPr>{''.join(rpr_items)}</w:rPr>" if rpr_items else ""
    return f'<w:r>{rpr_str}<w:t xml:space="preserve">{escaped}</w:t></w:r>'


def runs_from_markdown(text, default_size=24, default_color="1E293B", default_bold=False, default_italic=False, is_rtl=True, font_name="Cairo"):
    """Convert inline markdown into a sequence of OpenXML <w:r> elements."""
    tokens = parse_inline_markdown(text)
    xml_runs = []
    for chunk, b, it, code in tokens:
        run_bold = default_bold or b
        run_italic = default_italic or it
        run_font = "Consolas" if code else font_name
        run_color = default_color
        if run_bold and default_color == "1E293B":
            run_color = "0F172A"
        xml_runs.append(build_run_xml(
            chunk,
            is_bold=run_bold,
            is_italic=run_italic,
            is_code=code,
            font_size=default_size,
            color=run_color,
            is_rtl=is_rtl,
            font_name=run_font
        ))
    return "".join(xml_runs)


def build_paragraph_xml(runs_xml, jc="right", is_bidi=True, spacing_before=0, spacing_after=120, line_spacing=360, bg_color=None, border_color=None, left_indent=0, right_indent=0):
    """Build OpenXML <w:p> paragraph with optional background, border, and indentation."""
    pPr_items = []
    if is_bidi:
        pPr_items.append('<w:bidi w:val="1"/>')
    if jc:
        pPr_items.append(f'<w:jc w:val="{jc}"/>')
    if spacing_before or spacing_after or line_spacing:
        pPr_items.append(f'<w:spacing w:before="{spacing_before}" w:after="{spacing_after}" w:line="{line_spacing}" w:lineRule="auto"/>')
    if left_indent or right_indent:
        pPr_items.append(f'<w:ind w:left="{left_indent}" w:right="{right_indent}"/>')
    if bg_color:
        pPr_items.append(f'<w:shd w:val="clear" w:color="auto" w:fill="{bg_color}"/>')
    if border_color:
        pPr_items.append(f'<w:pBdr><w:right w:val="single" w:sz="24" w:space="12" w:color="{border_color}"/></w:pBdr>')

    pPr_str = f"<w:pPr>{''.join(pPr_items)}</w:pPr>" if pPr_items else ""
    return f'<w:p>{pPr_str}{runs_xml}</w:p>'


def build_table_xml(rows_data, col_widths=None):
    """
    Build OpenXML <w:tbl> table with clean borders, header shading, and RTL support.
    rows_data: list of list of str (cell markdown texts)
    """
    if not rows_data or not rows_data[0]:
        return ""

    num_cols = len(rows_data[0])
    tbl_xml = ['<w:tbl>']

    tbl_xml.append(
        '<w:tblPr>'
        '<w:bidiVisual/>'
        '<w:jc w:val="center"/>'
        '<w:tblW w:w="9800" w:type="dxa"/>'
        '<w:tblBorders>'
        '  <w:top w:val="single" w:sz="8" w:space="0" w:color="CBD5E1"/>'
        '  <w:left w:val="single" w:sz="8" w:space="0" w:color="CBD5E1"/>'
        '  <w:bottom w:val="single" w:sz="8" w:space="0" w:color="CBD5E1"/>'
        '  <w:right w:val="single" w:sz="8" w:space="0" w:color="CBD5E1"/>'
        '  <w:insideH w:val="single" w:sz="6" w:space="0" w:color="E2E8F0"/>'
        '  <w:insideV w:val="single" w:sz="6" w:space="0" w:color="E2E8F0"/>'
        '</w:tblBorders>'
        '<w:tblCellMar>'
        '  <w:top w:w="140" w:type="dxa"/>'
        '  <w:bottom w:w="140" w:type="dxa"/>'
        '  <w:left w:w="180" w:type="dxa"/>'
        '  <w:right w:w="180" w:type="dxa"/>'
        '</w:tblCellMar>'
        '</w:tblPr>'
    )

    tbl_xml.append('<w:tblGrid>')
    cell_w = 9800 // num_cols
    for i in range(num_cols):
        w = col_widths[i] if col_widths and i < len(col_widths) else cell_w
        tbl_xml.append(f'<w:gridCol w:w="{w}"/>')
    tbl_xml.append('</w:tblGrid>')

    for r_idx, row in enumerate(rows_data):
        is_header = (r_idx == 0)
        row_bg = "0284C7" if is_header else ("F8FAFC" if r_idx % 2 == 1 else "FFFFFF")
        text_color = "FFFFFF" if is_header else "1E293B"

        tbl_xml.append('<w:tr>')
        if is_header:
            tbl_xml.append('<w:trPr><w:tblHeader/></w:trPr>')

        for c_idx, cell_text in enumerate(row):
            w = col_widths[c_idx] if col_widths and c_idx < len(col_widths) else cell_w
            cell_runs = runs_from_markdown(cell_text.strip(), default_size=22 if is_header else 21, default_color=text_color, default_bold=is_header)
            cell_p = build_paragraph_xml(cell_runs, jc="right", is_bidi=True, spacing_before=40, spacing_after=40, line_spacing=280)

            tbl_xml.append(
                f'<w:tc>'
                f'<w:tcPr>'
                f'  <w:tcW w:w="{w}" w:type="dxa"/>'
                f'  <w:shd w:val="clear" w:color="auto" w:fill="{row_bg}"/>'
                f'  <w:vAlign w:val="center"/>'
                f'</w:tcPr>'
                f'{cell_p}'
                f'</w:tc>'
            )
        tbl_xml.append('</w:tr>')

    tbl_xml.append('</w:tbl>')
    return "".join(tbl_xml)


def convert_markdown_to_docx_document_xml(md_text, doc_title="Video Script"):
    """
    Parse markdown content and convert to OpenXML body elements tailored for video scripts:
    - Hero Title & Subtitle banner
    - Clean RTL metadata tables
    - Prominent scene markers [HOOK], [INTRO]
    - Amber callout boxes for director & camera cues
    - Highlighted speaker tags & dialogue
    - Monospace cards for copyable SEO text & timestamps
    """
    body_elements = []

    # 1. Hero Title Banner
    hero_runs = build_run_xml(doc_title, is_bold=True, font_size=42, color="0284C7", is_rtl=True, font_name="Cairo")
    body_elements.append(build_paragraph_xml(hero_runs, jc="center", spacing_before=300, spacing_after=80, line_spacing=380))

    subtitle_runs = build_run_xml("حزمة السكريبت المعتمدة • جاهزة للإنتاج والتصوير", is_bold=True, font_size=22, color="64748B", is_rtl=True, font_name="Cairo")
    body_elements.append(build_paragraph_xml(subtitle_runs, jc="center", spacing_before=0, spacing_after=300, line_spacing=300))

    lines = md_text.splitlines()
    i = 0
    in_code_block = False
    code_block_lines = []

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Handle Code Block fences ```
        if stripped.startswith('```'):
            if not in_code_block:
                in_code_block = True
                code_block_lines = []
            else:
                in_code_block = False
                tag_run = build_run_xml("نص قابل للنسخ / Copyable Content", is_bold=True, font_size=18, color="64748B", is_rtl=False, font_name="Consolas")
                body_elements.append(build_paragraph_xml(tag_run, jc="left", is_bidi=False, spacing_before=160, spacing_after=40, bg_color="F1F5F9", left_indent=200, right_indent=200))

                for cl in code_block_lines:
                    c_run = build_run_xml(cl if cl else " ", is_code=True, font_size=19, color="334155", is_rtl=False, font_name="Consolas")
                    body_elements.append(build_paragraph_xml(c_run, jc="left", is_bidi=False, spacing_before=20, spacing_after=20, line_spacing=260, bg_color="F8FAFC", left_indent=200, right_indent=200))

                body_elements.append(build_paragraph_xml("", spacing_before=0, spacing_after=120))
            i += 1
            continue

        if in_code_block:
            code_block_lines.append(line)
            i += 1
            continue

        if not stripped:
            i += 1
            continue

        # Horizontal Rule
        if stripped in ('---', '***', '___') or re.match(r'^[-*_]{3,}$', stripped):
            sep_p = build_paragraph_xml("", spacing_before=140, spacing_after=140, border_color="E2E8F0")
            body_elements.append(sep_p)
            i += 1
            continue

        # Tables: detect lines with |
        if stripped.startswith('|') and stripped.endswith('|'):
            table_lines = []
            while i < len(lines) and lines[i].strip().startswith('|') and lines[i].strip().endswith('|'):
                table_lines.append(lines[i].strip())
                i += 1

            parsed_rows = []
            for tl in table_lines:
                cleaned = re.sub(r'[\s\-:|]', '', tl)
                if not cleaned:
                    continue
                cells = [c.strip() for c in tl.strip('|').split('|')]
                parsed_rows.append(cells)

            if parsed_rows:
                col_widths = [3500, 6300] if len(parsed_rows[0]) == 2 else None
                body_elements.append(build_table_xml(parsed_rows, col_widths=col_widths))
                body_elements.append(build_paragraph_xml("", spacing_before=0, spacing_after=160))
            continue

        # Headings
        h_match = re.match(r'^(#{1,6})\s+(.*)', stripped)
        if h_match:
            level = len(h_match.group(1))
            heading_text = h_match.group(2).strip()

            if level == 1 and heading_text == doc_title:
                i += 1
                continue

            if level == 1:
                runs = runs_from_markdown(heading_text, default_size=36, default_color="0284C7", default_bold=True)
                body_elements.append(build_paragraph_xml(runs, jc="right", spacing_before=360, spacing_after=160, line_spacing=380, border_color="0284C7"))
            elif level == 2:
                runs = runs_from_markdown(heading_text, default_size=30, default_color="0F172A", default_bold=True)
                body_elements.append(build_paragraph_xml(runs, jc="right", spacing_before=300, spacing_after=140, line_spacing=360, border_color="0284C7"))
            elif level == 3:
                runs = runs_from_markdown(heading_text, default_size=26, default_color="0369A1", default_bold=True)
                body_elements.append(build_paragraph_xml(runs, jc="right", spacing_before=240, spacing_after=100, line_spacing=340))
            else:
                runs = runs_from_markdown(heading_text, default_size=23, default_color="334155", default_bold=True)
                body_elements.append(build_paragraph_xml(runs, jc="right", spacing_before=200, spacing_after=80, line_spacing=320))
            i += 1
            continue

        # Scene Markers like **[HOOK]**, **[INTRO]**, **[SCENE 1]**
        scene_match = re.match(r'^\*{2}\[(.*?)\]\*{2}$', stripped) or re.match(r'^\[([A-Z0-9\s:_\-]+)\]$', stripped)
        if scene_match:
            scene_name = scene_match.group(1).strip()
            badge_runs = build_run_xml(f"⚡  [{scene_name}]", is_bold=True, font_size=23, color="7E22CE", is_rtl=True, font_name="Cairo")
            body_elements.append(build_paragraph_xml(
                badge_runs,
                jc="right",
                spacing_before=260,
                spacing_after=120,
                line_spacing=320,
                bg_color="F3E8FF",
                border_color="A855F7",
                left_indent=160,
                right_indent=160
            ))
            i += 1
            continue

        # Director & Camera Cues e.g. **(الكاميرا قريبة...)** or *(موسيقى...)*
        cue_match = re.match(r'^\*{1,2}\((.*?)\)\*{1,2}$', stripped)
        if cue_match:
            cue_text = cue_match.group(1).strip()
            cue_runs = build_run_xml(f"🎬  ({cue_text})", is_italic=True, font_size=21, color="854D0E", is_rtl=True, font_name="Cairo")
            body_elements.append(build_paragraph_xml(
                cue_runs,
                jc="right",
                spacing_before=140,
                spacing_after=140,
                line_spacing=320,
                bg_color="FEF9C3",
                border_color="EAB308",
                left_indent=160,
                right_indent=160
            ))
            i += 1
            continue

        # Speaker Dialogue e.g. **د. أحمد:** كلام ...
        speaker_match = re.match(r'^\*\*(د\.\s*[\u0600-\u06FFa-zA-Z]+|المذيع|الراوي|المريض|Dr\.\s*[\w]+):\*\*\s*(.*)', stripped)
        if speaker_match:
            speaker_name = speaker_match.group(1).strip()
            dialogue_text = speaker_match.group(2).strip()

            speaker_run = build_run_xml(f"🎙️ {speaker_name}: ", is_bold=True, font_size=24, color="0284C7", is_rtl=True, font_name="Cairo")
            dialogue_runs = runs_from_markdown(dialogue_text, default_size=24, default_color="0F172A")
            full_runs = speaker_run + dialogue_runs

            body_elements.append(build_paragraph_xml(
                full_runs,
                jc="right",
                spacing_before=140,
                spacing_after=140,
                line_spacing=380
            ))
            i += 1
            continue

        # Lists (Bullets and Numbered)
        bullet_match = re.match(r'^([\*\-]\s+)(.*)', stripped)
        num_match = re.match(r'^(\d+\.\s+)(.*)', stripped)
        if bullet_match:
            prefix = "•  "
            content = bullet_match.group(2)
            bullet_run = build_run_xml(prefix, is_bold=True, font_size=23, color="0284C7", is_rtl=True)
            text_runs = runs_from_markdown(content, default_size=23, default_color="1E293B")
            body_elements.append(build_paragraph_xml(bullet_run + text_runs, jc="right", spacing_before=50, spacing_after=70, line_spacing=320, right_indent=360))
            i += 1
            continue
        elif num_match:
            prefix = num_match.group(1) + " "
            content = num_match.group(2)
            num_run = build_run_xml(prefix, is_bold=True, font_size=23, color="0284C7", is_rtl=True)
            text_runs = runs_from_markdown(content, default_size=23, default_color="1E293B")
            body_elements.append(build_paragraph_xml(num_run + text_runs, jc="right", spacing_before=60, spacing_after=80, line_spacing=320, right_indent=360))
            i += 1
            continue

        # Regular paragraph
        p_runs = runs_from_markdown(stripped, default_size=23, default_color="1E293B")
        body_elements.append(build_paragraph_xml(p_runs, jc="right", spacing_before=60, spacing_after=140, line_spacing=360))
        i += 1

    # Page Setup (A4, 1-inch margins)
    body_elements.append(
        '<w:sectPr>'
        '  <w:pgSz w:w="11906" w:h="16838"/>'
        '  <w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" w:header="720" w:footer="720" w:gutter="0"/>'
        '</w:sectPr>'
    )

    return "".join(body_elements)


def export_markdown_to_docx(md_path_or_text, docx_path, doc_title=None):
    """
    Main export function: Converts Markdown into a styled, professional OpenXML Word (.docx) file.
    Creates all OpenXML archive parts with zero external library dependencies.
    """
    if os.path.exists(md_path_or_text):
        with open(md_path_or_text, "r", encoding="utf-8") as f:
            md_text = f.read()
    else:
        md_text = md_path_or_text

    if not doc_title:
        title_match = re.search(r'Video Title.*?[|:]\s*([^\n|]+)', md_text)
        doc_title = title_match.group(1).strip() if title_match else "نص الفيديو - Production Script"

    body_xml = convert_markdown_to_docx_document_xml(md_text, doc_title=doc_title)

    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
  <Override PartName="/word/settings.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>"""

    root_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>"""

    word_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings" Target="settings.xml"/>
</Relationships>"""

    settings = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:settings xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:defaultTabStop w:val="720"/>
  <w:characterSpacingControl w:val="doNotCompress"/>
</w:settings>"""

    created_iso = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    core_props = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
                   xmlns:dc="http://purl.org/dc/elements/1.1/"
                   xmlns:dcterms="http://purl.org/dc/terms/"
                   xmlns:dcmitype="http://purl.org/dc/dcmitype/"
                   xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:title>{escape_xml(doc_title)}</dc:title>
  <dc:creator>Medical Brain Script Exporter</dc:creator>
  <cp:lastModifiedBy>Medical Brain</cp:lastModifiedBy>
  <dcterms:created xsi:type="dcterms:W3CDTF">{created_iso}</dcterms:created>
</cp:coreProperties>"""

    app_props = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">
  <Application>Medical Brain Script Exporter</Application>
  <DocSecurity>0</DocSecurity>
</Properties>"""

    styles = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:docDefaults>
    <w:rPrDefault>
      <w:rPr>
        <w:rFonts w:ascii="Cairo" w:hAnsi="Cairo" w:cs="Cairo"/>
        <w:sz w:val="24"/>
        <w:szCs w:val="24"/>
        <w:color w:val="1E293B"/>
        <w:rtl w:val="1"/>
      </w:rPr>
    </w:rPrDefault>
    <w:pPrDefault>
      <w:pPr>
        <w:bidi w:val="1"/>
        <w:jc w:val="right"/>
        <w:spacing w:line="360" w:lineRule="auto" w:after="140"/>
      </w:pPr>
    </w:pPrDefault>
  </w:docDefaults>
</w:styles>"""

    document = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
            xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <w:body>
    {body_xml}
  </w:body>
</w:document>"""

    out_dir = os.path.dirname(os.path.abspath(docx_path))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    with zipfile.ZipFile(docx_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", root_rels)
        zf.writestr("word/_rels/document.xml.rels", word_rels)
        zf.writestr("word/settings.xml", settings)
        zf.writestr("word/styles.xml", styles)
        zf.writestr("word/document.xml", document)
        zf.writestr("docProps/core.xml", core_props)
        zf.writestr("docProps/app.xml", app_props)

    return docx_path


def convert_markdown_file(md_path, html_path=None, pdf_path=None, docx_path=None):
    """
    Main function to convert an input Markdown file into HTML, PDF, and DOCX.
    - Saves in the same directory with the same base name if html_path/pdf_path/docx_path not specified.
    - Returns (html_path, pdf_path, docx_path).
    """
    md_path = os.path.abspath(md_path)
    if not os.path.exists(md_path):
        raise FileNotFoundError(f"Markdown file does not exist: {md_path}")

    base, _ = os.path.splitext(md_path)
    if not html_path:
        html_path = base + ".html"
    if not pdf_path:
        pdf_path = base + ".pdf"
    if not docx_path:
        docx_path = base + ".docx"

    print(f"\n📖 Reading markdown from: {md_path}")
    with open(md_path, "r", encoding="utf-8") as f:
        md_text = f.read()

    print("🎨 Formatting content and generating responsive HTML layout...")
    doc_title, processed_body = enhance_markdown_content(md_text)
    full_html = build_full_html_document(doc_title, processed_body)

    with open(html_path, "w", encoding="utf-8") as f:
        f.write(full_html)
    print(f"✅ HTML file created: {html_path} ({os.path.getsize(html_path):,} bytes)")

    print("📑 Exporting to PDF via headless Chrome...")
    generated_pdf = export_html_to_pdf(html_path, pdf_path)
    if generated_pdf:
        print(f"✅ PDF file created:  {generated_pdf} ({os.path.getsize(generated_pdf):,} bytes)")
    else:
        print(f"⚠️ PDF generation was skipped or encountered an issue. HTML is fully available.")

    print("📝 Exporting to Word (.docx) with native Arabic RTL formatting...")
    try:
        generated_docx = export_markdown_to_docx(md_text, docx_path, doc_title=doc_title)
        if generated_docx and os.path.exists(generated_docx):
            print(f"✅ DOCX file created: {generated_docx} ({os.path.getsize(generated_docx):,} bytes)")
        else:
            generated_docx = None
            print("⚠️ DOCX generation was skipped or produced no output.")
    except Exception as docx_err:
        print(f"⚠️ DOCX export warning: {docx_err}")
        generated_docx = None

    return html_path, generated_pdf, generated_docx


def main():
    """Terminal entry point with interactive prompt or argument support."""
    if len(sys.argv) > 1 and sys.argv[1].strip() and not sys.argv[1].startswith("-"):
        md_file = sys.argv[1].strip().strip('"').strip("'")
    else:
        print("=" * 65)
        print("🎬 Script Exporter: Markdown -> Responsive HTML + PDF + DOCX")
        print("=" * 65)
        try:
            user_input = input("\n📁 Please enter the path of the .md file to convert: ")
            md_file = user_input.strip().strip('"').strip("'")
        except (KeyboardInterrupt, EOFError):
            print("\nOperation cancelled.")
            sys.exit(0)

    if not md_file:
        print("❌ Error: No file path provided.")
        sys.exit(1)

    # Expand user tilde ~
    md_file = os.path.expanduser(md_file)

    if not os.path.exists(md_file):
        print(f"❌ Error: File not found: {md_file}")
        sys.exit(1)

    try:
        html_path, pdf_path, docx_path = convert_markdown_file(md_file)
        print("\n" + "=" * 65)
        print("✨ Conversion completed successfully!")
        print(f"📄 HTML: {html_path}")
        if pdf_path:
            print(f"📑 PDF:  {pdf_path}")
        if docx_path:
            print(f"📝 DOCX: {docx_path}")
        print("=" * 65 + "\n")
    except Exception as e:
        print(f"\n❌ Error during conversion: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()

