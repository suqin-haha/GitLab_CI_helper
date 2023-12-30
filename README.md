# GitLab_CI_helper
This script helps you run the minimum possible pipeline to target a specific job (or number of jobs) from a specific branch/commit (commit from current branch). It could also let you repeat jobs multiple times. If run without arguments, it’ll default to running the minimum pipeline required to run all the FAILED jobs from the current branch (if a pipeline exists). This script was specifically created to help engineers debug CI pipeline failures in the most resource efficient way possible (less compute resources per pipeline and less time to wait for a job to run).

## Instructions

First you’ll need to authenticate to gitlab.com using glab (in nix-shell) and follow the instructions in the CLI:

`./run.sh glab auth login`



Run gitlab_ci_helper.py -h  in project’s root directory to see detailed help message.

Usage to target failed jobs ( script success signal is  green Push success! and blue Recovered): 

`gitlab_ci_helper.py`

Usage to target specific jobs (job name format is same with the name in Gitlab): 

`gitlab_ci_helper.py -r 3 -j 'lint:python, itest:clustering-sequential: [freya/cloudstorage/cluster_tests/tasks]'`



#### Note:

The script support to be used in outside nix-shell with `run-in-nix-shell.sh`.  Recommend enter nix-shell first for speed and compatibility.

GitLab have limit for matrix,  so (repeat number) * (package number/parallel number) need to < 200

Double check the pipeline URL, it’s not very reliable. Sometimes glab return URL for another pipeline. 

Current way we repeating jobs is  by adding variable REPEAT in parallel:matrix , beware of conflict.

Before



Clustering job failed

After



 

## How does it work?

Dependencies: python3, glab(gitlab CLI), pyyaml, and git.

Get target jobs by CLI args or run glab command to get failed jobs from pipeline

Get all necessary jobs by DFS search the required pre-jobs, and clean sub-jobs

git stash save work

Write all the jobs back (cleaned and repeated)

git add, git commit, git push to produce a pipeline

git revert back, and pop git stash

