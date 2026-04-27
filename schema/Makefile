.PHONY: schema lint breaking

schema:
	buf generate

lint:
	buf lint

breaking:
	buf breaking --against '.git#branch=main,subdir=schema'
