from pathlib import Path
import re

root = Path(r'C:\Users\IT-PC\Documents\integralcarte\geoad')
pattern = re.compile(r'class=("|\')(.*?)(?<!\\)\1')

for p in list(root.rglob('*.html')):
    try:
        text = p.read_text(encoding='utf-8')
    except Exception:
        continue

    def repl(match):
        quote = match.group(1)
        val = match.group(2)
        parts = val.split()
        if 'badge-status' in parts and 'badge' not in parts:
            parts.insert(0, 'badge')
        return f'class={quote}{" ".join(parts)}{quote}'

    new_text = pattern.sub(repl, text)
    if new_text != text:
        p.write_text(new_text, encoding='utf-8')

print('OK')
