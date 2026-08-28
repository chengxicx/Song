-- Add per-language TTS and Google Translate target settings to the
-- languages table.  Both columns are optional: when empty, the
-- behaviour is unchanged (TTS tag derived from the language name,
-- translation target taken from the browser UI language).

-- Custom BCP-47 tag used for TTS voice matching (browser
-- SpeechSynthesis and the edge-tts /tts/ fallback), overriding the
-- built-in language-name lookup.  E.g. "zh-HK" for Cantonese, "yue".
ALTER TABLE languages ADD COLUMN LgTTSLang TEXT;

-- Custom Google Translate target language for the term popup
-- auto-translate, overriding the browser UI language.
-- E.g. "zh-CN" to translate Cantonese terms into Mandarin.
ALTER TABLE languages ADD COLUMN LgTranslateTargetLang TEXT;
