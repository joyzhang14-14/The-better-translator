import deepl

auth_key = "adef608f-1d8b-4831-94a2-37a6992c77d8:fx"
translator = deepl.Translator(auth_key)

result = translator.translate_text("Pg交出來", target_lang="EN-US")
print(result.text)