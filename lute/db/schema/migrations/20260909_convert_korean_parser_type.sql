-- Korean parser moved from built-in core to the lute3-korean plugin,
-- so the parser_type key is now 'lute_korean' instead of 'korean'.
UPDATE languages SET LgParserType='lute_korean' WHERE LgParserType='korean';