"""Display formatting only; preserves names already entered with mixed case."""
def display_name(value):
    text=' '.join(str(value or '').split())
    if text and (text.isupper() or text.islower()):
        words=text.title().split()
        return ' '.join(word.lower() if i and word.lower() in {'de','del','la','las','los','y'} else word for i,word in enumerate(words))
    return text
