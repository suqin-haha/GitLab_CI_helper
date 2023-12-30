#!/usr/bin/env bash

if [[ -z $IN_NIX_SHELL  && -z $NIX_STORE && $HOST != *"/nix/store/"* && $PATH != *"/nix/store/"* ]]
then
      # wrap argument with "" avoid problem about space in argument, eg. itest:end-to-end 1/4
      PARAMS=""
      for PARAM in "$@"
      do 
            PARAMS="${PARAMS} \"$PARAM\""
      done
	# not in nix-shell, run command in nix-shell
      echo "Not in nix-shell (recommend enter nix-shell frist), enter nix-shell and run: 
      gitlab_ci_helper.py ${PARAMS}" 
      nix-shell --run "gitlab_ci_helper.py ${PARAMS}"
else
      # in nix-shell, direct run the script
      echo In nix-shell, run: gitlab_ci_helper.py "$@"
      gitlab_ci_helper.py "$@"
fi
