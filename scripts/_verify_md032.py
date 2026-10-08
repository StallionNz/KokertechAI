import sys
sys.path.insert(0, 'scripts')
import fix_markdownlint as fml

D = chr(10)

text = (
    'intro paragraph' + D
    + '```yaml' + D
    + '- step1' + D
    + '- step2' + D
    + '```' + D
    + 'after block' + D
)
out, changed = fml.fix_md032(text)
print('=== USER REGRESSION CASE: YAML fence with - step1 / - step2 inside ===')
print('INPUT    : ' + repr(text))
print('OUTPUT   : ' + repr(out))
print('CHANGED  : ' + str(changed))
print('VERDICT  : ' + ('PASS - no spurious blanks inside fence' if out == text else 'FAIL - blanks were injected'))
