"""Exercise the imported checker against offline and executable output."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

CHECKER = Path(__file__).resolve().parents[1] / 'skills/diagram-design/scripts/self_check.py'
DOCUMENT = '''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>中文架构验证</title><style>body {font-family: "Noto Sans CJK SC", system-ui, sans-serif;}
svg {min-width: 400px;} .diagram {overflow-x: auto;}</style></head><body>
<h1>中文架构验证</h1><div class="diagram"><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 160" role="img" aria-labelledby="smoke-title smoke-desc">
<title id="smoke-title">中文架构验证</title><desc id="smoke-desc">客户端通过接口访问服务端。</desc>
<line x1="144" y1="80" x2="256" y2="80" stroke="#4f5d75"/>
<rect x="24" y="48" width="120" height="64" fill="#f5f5f5" stroke="#2d3142"/>
<rect x="256" y="48" width="120" height="64" fill="#f5f5f5" stroke="#2d3142"/>
<text x="48" y="84">客户端</text><text x="280" y="84">服务端</text>
</svg></div></body></html>'''

class DiagramDesignTests(unittest.TestCase):
    def check_document(self, text):
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / 'smoke.html'
            file.write_text(text, encoding='utf-8')
            return subprocess.run([sys.executable, str(CHECKER), '--offline', str(file)],
                                  capture_output=True, text=True,
                                  env={**os.environ, 'PYTHONDONTWRITEBYTECODE':'1'}, timeout=15)

    def test_chinese_system_font_output_is_accepted(self):
        result = self.check_document(DOCUMENT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_remote_font_is_rejected_in_offline_mode(self):
        result = self.check_document(DOCUMENT.replace('</head>', '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Geist"></head>'))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('offline: file loads Google Fonts', result.stdout)

    def test_executable_attribute_is_rejected(self):
        result = self.check_document(DOCUMENT.replace('<svg ', '<svg onload="alert(1)" ', 1))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('executable attribute onload', result.stdout)
