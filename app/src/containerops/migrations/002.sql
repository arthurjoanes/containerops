-- A release 1 usa colunas explícitas e continua compatível com esta migração.
ALTER TABLE jobs ADD COLUMN algorithm varchar(64);
UPDATE schema_version SET version = 2;

