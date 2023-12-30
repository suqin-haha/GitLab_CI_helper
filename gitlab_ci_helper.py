#!/usr/bin/env python3
import os
import sys
import subprocess
import typing
import re
import traceback
import unittest

try:
    import yaml
except ImportError:
    sys.exit("PyYAML dependency is missing!")

import argparse
from time import sleep

# paths
# dirToYaml = os.getcwd() + "/gitlab/"

# Reference: https://death.andgravity.com/any-yaml
# add new type to customize Loader and Dumber for Yaml File reading and writing
class PipeLoader(yaml.FullLoader):
    pass


class PipeDumper(yaml.Dumper):
    pass


# new type for tag
class Tagged(typing.NamedTuple):
    tag: str
    value: object


# construct function for tag Loading
def construct_undefined(self, suffix, node):
    if isinstance(node, yaml.nodes.ScalarNode):
        value = self.construct_scalar(node)
    elif isinstance(node, yaml.nodes.SequenceNode):
        value = self.construct_sequence(node)
    elif isinstance(node, yaml.nodes.MappingNode):
        value = self.construct_mapping(node)
    else:
        assert False, f"unexpected node: {node!r}"
    return Tagged(node.tag, value)


# represent function for tag Dumping
def represent_tagged(self, data):
    assert isinstance(data, Tagged), data
    node = self.represent_data(data.value)
    node.tag = data.tag
    return node


# colors used present nice message
class Bcolors:
    HEADER = "\033[95m"
    OKBLUE = "\033[94m"
    OKCYAN = "\033[96m"
    OKGREEN = "\033[92m"
    WARNING = "\033[93m"
    FAIL = "\033[91m"
    ENDC = "\033[0m"
    BOLD = "\033[1m"
    UNDERLINE = "\033[4m"

# Constant Keywords 
SCRIPT = "script"
NEEDS = "needs"
DEPEN = "dependencies"
EXTENDS = "extends"
BACKUPSUFF = ".backup"
PARALLEL = "parallel"
MATRIX = "matrix"
SUCCESS = "success"
FAILED = "failed"
MANUAL = "manual"
UTF_8 = "utf-8"
ENDTOENDPARALLEL = 4


def isRemoveableJob(block, key):
    return type(block) is dict and ((SCRIPT in block or ":" in key)) and key[0] != "."


def getListOfYamlFiles(dirToYaml):
    fileList = []
    for fn in os.listdir(dirToYaml):
        if fn.endswith(".yml"):
            fileList.append(fn)
    return fileList


# get all from yamlfiles as object to process
def getAllConfig(yamlFiles, dirToYaml):
    jobs = {}
    validFileList = []
    for fn in yamlFiles:
        with open(dirToYaml + fn, "r") as o:
            y = yaml.load(o.read(), Loader=PipeLoader)
            if y != None:
                validFileList.append(fn)
                for j in y:
                    jobs[j] = y[j]

    yamlFiles[:] = validFileList
    return jobs


# getDependencies: get all dependency jobs for the target job
# INPUT: single target name, all jobs
# OUTPUT: a list of minimum dependency jobs's name
def getDependencies(target, yObject):
    list = []
    if NEEDS in yObject[target]:
        for need in yObject[target][NEEDS]:
            # check job section
            if type(need) is dict:
                list.extend(getDependencies(need["job"], yObject))
            else:
                list.extend(getDependencies(need, yObject))
    if DEPEN in yObject[target]:
        for d in yObject[target][DEPEN]:
            list.extend(getDependencies(d, yObject))
    if EXTENDS in yObject[target]:
        if isinstance(
            yObject[target][EXTENDS], str
        ):  # if the extend is referencing other jobs
            list.extend(getDependencies(yObject[target][EXTENDS], yObject))
        else:
            for extend in yObject[target][EXTENDS]:
                list.extend(getDependencies(extend, yObject))
    list.append(target)
    return list


# add REPEAT in matrix. if parallel is number then make the number = repeatNum * 4 (which is end-to-end job parallel number)
def addRepeat(blocks, key, repeatNum):
    repeatList = list(range(0, repeatNum))
    parallelDic = {"matrix": [{"REPEAT": repeatList}]}
    if "parallel" not in blocks[key]:
        blocks[key]["parallel"] = parallelDic
    else:
        if isinstance(blocks[key]["parallel"], int):
            blocks[key]["parallel"] = repeatNum * blocks[key]["parallel"]
        else:
            blocks[key]["parallel"]["matrix"][0]["REPEAT"] = repeatList


# selectWriteBack: read files again, then write back with: first skip unnecessary jobs, and for the rest jobs, select from cleaned matrix jobs and repeat target jobs
# INPUT: jobs in minimum path, all jobs with reduced/cleaned matrix, yaml files list
def selectWriteBack(dirToYaml, minimumJobs, cleanedJobs, yamlFiles, targetJobs, repeatNum):
    for fn in yamlFiles:
        # filter to get mini blocks for the file
        newBlocks = {}
        with open(dirToYaml + fn, "r") as f:
            blocks = yaml.load(f.read(), Loader=PipeLoader)
            for bKey in blocks:
                if bKey in minimumJobs:
                    if bKey in cleanedJobs:
                        newBlocks[bKey] = cleanedJobs[bKey]
                        if bKey in targetJobs and repeatNum > 0:
                            addRepeat(newBlocks, bKey, repeatNum)
                    else:
                        newBlocks[bKey] = blocks[bKey]
                        if bKey in targetJobs and repeatNum > 0:
                            addRepeat(newBlocks, bKey, repeatNum)

        # put a place holder for the file, if we removed all origin content of the file
        if newBlocks == {}:
            newBlocks[".emptyPlaceHolder"] = {}
            newBlocks[".emptyPlaceHolder"]["variables"] = []

        # write new back
        with open(dirToYaml + fn, "w") as f:
            yaml.dump(
                newBlocks,
                f,
                sort_keys=False,
                allow_unicode=True,
                encoding=UTF_8,
                Dumper=PipeDumper,
            )


# splitArgument: split the targetName and subjob out of input
# INPUT: 'targetName:[subjob]' or 'targetName parallel-part'
# OUTPUT: targetName, subjob
def splitArgument(input):
    if "[" in input or "]" in input:
        input = input.replace(" ", "")
        input = input.replace("]", "")
        result = input.rsplit(":[", 1)
        if len(result) > 2:
            sys.exit(
                f"{Bcolors.FAIL}[Error] Multiple ':[' in one job name, please check input "
                + f"{Bcolors.ENDC}"
            )
        elif len(result) == 2:
            if "," in result[1]:
                subjobs = result[1].split(",")
                return result[0], subjobs[0]
            elif result[1].isnumeric():
                return result[0], ""
            else:
                return result[0], result[1]
        else:
            return result[0], ""
    if " " in input:
        return input.split(" ")[0], ""
    return input, ""


#  cleanMatrix: if there are jobs(keys of TargetDic) mentioned in TargetDic then only keep subjobs (values of TargetDic) mentioned in TargetsDic
#  INPUT: all the jobs, the dic of targets and their subjobs
#  OUTPUT: all the jobs after remove unnecessary subjobs
#  Note : subjob for the target job may empty which means needs all subjobs, even the target job show up with subjob again, we still need to keep all of them
def cleanMatrix(jobs, TargetsDic):
    for jobName in TargetsDic:
        subjobs = TargetsDic[jobName]
        if "" in subjobs:
            continue
        block = jobs[jobName]
        for m in block[PARALLEL][MATRIX]:
            # find correct section
            for mk in m:
                if list(subjobs)[0] in list(m[mk]):
                    # create new matrix
                    newm = []
                    for pkg in list(m[mk]):
                        if pkg in subjobs:
                            newm.append(pkg)
                    # replace matrix
                    m[mk] = newm
    return jobs


# get failed list from glab-cli by branch or commit
def getFailedListFromGlab(branch=""):
    print(
        "Getting failed jobs from: " + f"{Bcolors.OKCYAN}" + branch + f"{Bcolors.ENDC}"
    )
    branchFlag = "--branch=" + branch
    result = subprocess.run(
        "glab ci status " + branchFlag, shell=True, capture_output=True
    )
    # check if pipeline state is running
    if "Pipeline State: running" in result.stdout.decode("utf-8"):
        while True:
            inputMsg = (
                "Pipeline is still running for branch/commit: "
                + f"{Bcolors.OKCYAN}"
                + branch
                + f"{Bcolors.ENDC}"
                + f", do you want to continue fetch failed jobs? (yes/no){Bcolors.ENDC}"
            )
            inputValue = str(input(inputMsg))
            if "n" == inputValue or "no" == inputValue:
                sys.exit(0)
            elif "y" == inputValue or "yes" == inputValue:
                break
            else:
                print(
                    f"{Bcolors.WARNING} Invalid input, answer y/yes, n/no{Bcolors.ENDC}"
                )
    result = subprocess.run(
        "glab ci status " + branchFlag + '| grep "(' + FAILED + ')"',
        shell=True,
        capture_output=True,
    )
    failStr = result.stdout.decode(UTF_8)
    if failStr == "":
        sys.exit(
            f"{Bcolors.FAIL}[Error] There is no failed job in branch/commit: "
            + branch
            + f"{Bcolors.ENDC}"
        )
    failList = failStr.split("\n")
    failedNames = []
    for failedJob in failList:
        # filter un-useful jobs
        if "\t\t" not in failedJob:
            continue
        arr = failedJob.split("\t\t")
        failedNames.append(arr[1].strip())
    return failedNames


# validate gitlab-cli is installed and verify user is authenticated
def validateGlab():
    result = subprocess.run("glab auth status", shell=True, capture_output=True)
    output = result.stderr.decode(UTF_8)
    if "No such file or directory" in output or "not found" in output:
        sys.exit(
            f"{Bcolors.FAIL}[Pre-Require Error] glab/gitlab-cli is missing, please re-enter nix-shell to reload nix configuration for nix pkg changing.{Bcolors.ENDC}"
        )
    outputArr = output.split("\n")
    for outputLine in outputArr:
        if outputLine.strip().startswith("x"):
            sys.exit(
                f"{Bcolors.FAIL}[Pre-Require Error] glab is not logged in. Please login using the instructions: 'https://gitlab.com/gitlab-org/cli#authentication'{Bcolors.ENDC}"
            )


# validate target job is exist, exit if anything wrong
def validateTargetJobs(targetName, subJobName, jobs):
    # validate jobs exist
    if targetName not in jobs:
        sys.exit(
            f"{Bcolors.FAIL}[Input Error] Job name["
            + targetName
            + f"] not found!  Please check, if you are using make, add arguments like this: args='job:[subjob], ...'{Bcolors.ENDC}"
        )
    if subJobName != "":
        block = jobs[targetName]
        if PARALLEL not in block:
            sys.exit(
                f"{Bcolors.FAIL}[Input Error] There is no Parallel section in "
                + targetName
                + f"{Bcolors.ENDC}"
            )
        if MATRIX not in block[PARALLEL]:
            sys.exit(
                f"{Bcolors.FAIL}[Input Error] There is no Matrix in "
                + targetName
                + +f"{Bcolors.ENDC}"
            )
        found = False
        for m in block[PARALLEL][MATRIX]:
            for mm in list(m.values()):
                if subJobName in mm:
                    found = True
        if found == False:
            sys.exit(
                f"{Bcolors.FAIL}"
                + "[Input Error] Subjob: "
                + subJobName
                + "not found in "
                + targetName
                + f"{Bcolors.ENDC}"
            )


def gitAdd(dirToYaml, debug):
    if debug:
        print(f"{Bcolors.WARNING}" + "Running git add" + f"{Bcolors.ENDC}")
    result = subprocess.run("git add " + dirToYaml, shell=True)
    if result.returncode != 0:
        sys.exit(
            f"{Bcolors.FAIL}"
            + "[Git Add Error] Error during 'git add' in [gitAdd]"
            + f"{Bcolors.ENDC}"
        )


# gitCommit: git commit with message for this script
# always run it until stderr catch return message
# case after run command:
# 	nothing to commit => stdout (return code 1)
# 	commit success => stdout (return code 0)
# 	commit failed => not tested
def gitCommit(targetJobs, debug, noVerify):
    # will take no commit to seccess
    if debug:
        print(f"{Bcolors.WARNING}" + "Running git commit" + f"{Bcolors.ENDC}")
    commitMsg = "[Don't merge this commit!] minimum pipeline for: " + ",".join(
        targetJobs
    )
    nv = ""
    if noVerify:
        nv = " --no-verify"
    if len(commitMsg) > 80:
        commitMsg = commitMsg[:75] + " ..."
    result = subprocess.run(
        'git commit -m "' + commitMsg + '"' + nv, shell=True, capture_output=True
    )
    if debug:
        print(result.stdout.decode(UTF_8))
    if result.returncode != 0:
        sys.exit(f"{Bcolors.FAIL}" + result.stderr.decode(UTF_8) + f"{Bcolors.ENDC}")
        print("commit return code: ", result.returncode)
    if result.returncode != 0:
        sys.exit(
            f"{Bcolors.FAIL}"
            + "git commit error: \n"
            + result.stderr.decode(UTF_8)
            + f"{Bcolors.ENDC}"
        )


# gitPush: handle git push with different cases
# case after run command:
# 	everything up to date => return code 0
# 	push successful => return code 0
# 	push failed => return code 1
def gitPush(debug):
    capture = True
    if debug:
        print(f"{Bcolors.WARNING}" + "Running git push" + f"{Bcolors.ENDC}")
        capture = False
    curBranch = gitGetBranch()
    result = subprocess.run(
        ["git", "push", "-f", "origin", curBranch], capture_output=capture
    )
    if result.returncode != 0:
        sys.exit(
            f"{Bcolors.FAIL}"
            + "[Git Push Error] There is an error during git push, if you need to create a new branch, use `make ci-minimum-pipeline failedFrom="
            + curBranch
            + "` to get failed job from current branch"
            + f"{Bcolors.ENDC}"
        )
    else:
        # check if there is MR in gitlab to run pipeline
        print(f"{Bcolors.OKGREEN}" + "Push success!" + f"{Bcolors.ENDC}")
        sleep(10)  # wait for gitlab update
        runPipeline(curBranch, debug)
        print("Recovering ...")


# after push to gitlab, need a short time let gitlab start pipeline. Then we can get correct pipeline URL
def printPipelineURL(branch):
    getUrl = subprocess.run(
        "glab ci status -b " + branch + " | grep 'https://gitlab.com/'",
        shell=True,
        capture_output=True,
    )
    if getUrl.returncode != 0:
        print(
            f"{Bcolors.FAIL}"
            + "[Error] error at [getPipelineURL]\n"
            + "Error Output:\n"
            + getUrl.stderr.decode(UTF_8)
            + f"{Bcolors.ENDC}"
        )
    else:
        print(
            f"Pipeline URL {Bcolors.WARNING}(double check if the pipeline url is correct!){Bcolors.ENDC}: "
            + f"{Bcolors.UNDERLINE}"
            + getUrl.stdout.decode(UTF_8).strip("\n")
            + f"{Bcolors.ENDC}"
        )


# get branch name as string by git
def gitGetBranch():
    branchObject = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True
    )
    if branchObject.returncode != 0:
        sys.exit(
            f"{Bcolors.FAIL}"
            + "[Error] Cannot get branch's name!\n"
            + "Error Output\n"
            + branchObject.stderr.decode("utf-8")
            + f"{Bcolors.ENDC}"
        )
    curBranch = branchObject.stdout.decode(UTF_8).strip()
    return curBranch


# check if there is a MR for current Branch, if not then run a pipeline for it
def runPipeline(curBranch, debug):
    if debug:
        print(f"{Bcolors.WARNING}" + "Checking MR exist" + f"{Bcolors.ENDC}")
    result = subprocess.run(
        ["glab", "mr", "list", "--source-branch=" + curBranch], capture_output=True
    )
    # if MR not exists then we need run pipeline, else MR exists gitlab should run MR automatically
    if "No open merge requests match your search" in result.stdout.decode("utf-8"):
        if debug:
            print(
                f"{Bcolors.WARNING}"
                + "[Warning] Do not find matching MR for this branch, this script is trying to create/run pipeline"
                + f"{Bcolors.ENDC}"
            )
        runpipe = subprocess.run(
            ["glab", "ci", "run", "-b", curBranch], capture_output=True
        )
        if debug:
            print(
                f"{Bcolors.UNDERLINE}"
                + runpipe.stdout.decode(UTF_8).strip()
                + f"{Bcolors.ENDC}"
            )
        if runpipe.returncode != 0:
            sys.exit(
                f"{Bcolors.FAIL}"
                + "[Error] Cannot automatically run pipeline for this branch, please run it manually!!\n"
                + "Error Output:\n"
                + runpipe.stderr.decode(UTF_8).strip("\n")
                + f"{Bcolors.ENDC}"
            )


# run git stash, if nothing to stash then return false
def gitStash(debug):
    if debug:
        print(
            f"{Bcolors.WARNING}Running git stash to save current uncommit work{Bcolors.ENDC}"
        )
    runStash = subprocess.run(["git", "stash"], capture_output=True)
    if "No local changes to save" in runStash.stdout.decode("utf-8"):
        return False
    print(runStash.stdout.decode("utf-8").strip("\n"))
    return True


def getCurCommit():
    run = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True)
    return run.stdout.decode("utf-8").strip("\n")


# Recover: reset back commit and pop stash if we saved one before
def resetBack(popStash, commit, debug):
    capture = True
    if debug:
        print(f"{Bcolors.WARNING}" + "Running git reset" + f"{Bcolors.ENDC}")
        capture = False
    subprocess.run(["git", "reset", "--hard", commit], capture_output=capture)
    if popStash:
        if debug:
            print(f"{Bcolors.WARNING}" + "Running git stash pop" + f"{Bcolors.ENDC}")
        subprocess.run(["git", "stash", "pop"], capture_output=capture)
    print(
        f"{Bcolors.OKBLUE}"
        + "Recovered. Everything should be same with before you run this script even it has failed \nYour current commit might behind with gitlab, if so, push with `-f` after test finishes"
        + f"{Bcolors.ENDC}"
    )


def gitlabCiHelper(dirToYaml):
    # customize yaml dumper for '!'
    PipeDumper.add_multi_representer(Tagged, represent_tagged)
    PipeLoader.add_multi_constructor("!", construct_undefined)

    targetJobs = []
    # command argument configuration
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="This script runs Gitlab ci pipeline with minimum test that only test failed or user input jobs. Job format: `job:[subjob]`\n"
        + "Example: gitlab-mini-test.py -j  'lint:python'",
    )
    parser.add_argument(
        "-j",
        "--jobs",
        default=False,
        type=str,
        help='run gitlab test for based on input jobs, followed by jobs in ""/\'\', split multi-value with ","',
    )
    parser.add_argument(
        "-d", "--debug", action="store_true", help="show more detailed debug messages"
    )
    parser.add_argument(
        "-f",
        "--failedFrom",
        default="HEAD",
        type=str,
        help="get Failed jobs from specific branch/commits, commits can only from current branch",
    )
    parser.add_argument(
        "-r",
        "--repeat",
        default=0,
        type=int,
        help="repeat times. `-r 2` task will runs in with [0, 1] twice. NOTE: in one job, (subjobs) * (repeatNum) MUST < 200",
    )
    parser.add_argument(
        "-n",
        "--no-verify",
        action="store_true",
        dest="noVerify",
        help="use the -n (--no-verify) flag when calling git commit",
    )

    # choose actions based on arguments
    args = parser.parse_args()
    debug = args.debug
    repeatNum = args.repeat
    if repeatNum < 0:
        sys.exit(
            f"{Bcolors.FAIL}[Input Error] Repeat number cannot be negative{Bcolors.ENDC}"
        )
    if debug:
        print(args)
    if args.jobs:
        # manual input jobs
        jobs = args.jobs
        jobs = jobs.replace("'", "")
        targetJobs = re.split(",(?![^[]*\])", jobs)
    else:
        # get job based on last pipeline
        failedFrom = args.failedFrom
        validateGlab()
        curbranch = gitGetBranch()
        if failedFrom == "HEAD":
            failedFrom = curbranch
        else:
            inputMsg = (
                "You are trying to generate failed jobs from branch/commit: "
                + f"{Bcolors.OKCYAN}"
                + failedFrom
                + f"{Bcolors.ENDC}"
                + " for current branch: "
                + f"{Bcolors.OKCYAN}"
                + curbranch
                + f"{Bcolors.ENDC}"
                + ", Do you want to continue?(yes/no)"
            )
            # double check when using failed job from the other branch/commit
            while True:
                inputValue = str(input(inputMsg))
                if "n" == inputValue or "no" == inputValue:
                    sys.exit(0)
                elif "y" == inputValue or "yes" == inputValue:
                    break
                else:
                    print(
                        f"{Bcolors.WARNING} Invalid input, answer y/yes, n/no{Bcolors.ENDC}"
                    )
        targetJobs = getFailedListFromGlab(failedFrom)

    if len(targetJobs) == 0:
        sys.exit(
            f"{Bcolors.FAIL}[Input Error] There is no target job to generate{Bcolors.ENDC}"
        )

    # get all jobs from file
    yamlFiles = getListOfYamlFiles(dirToYaml)
    jobs = getAllConfig(yamlFiles, dirToYaml)
    # get find job title
    miniJobsNames = set()
    targetsDic = {}
    if debug:
        print("Input Jobs: ")
        print(targetJobs)

    targetJobTitles = []
    for target in targetJobs:
        # split job names
        target = target.strip()
        targetName, subjob = splitArgument(target)
        # used for message
        targetJobTitles.append(targetName + ": " + subjob)

        validateTargetJobs(targetName, subjob, jobs)
        # get organized job:subjobs map
        if targetName not in targetsDic:
            targetsDic[targetName] = set()
        targetsDic[targetName].add(subjob)
        # get minimum required jobs
        miniJobsNames = miniJobsNames.union(getDependencies(targetName, jobs))

    print(
        "This program will generate Gitlab configuration files for these target jobs:"
    )
    print(set(targetJobTitles))

    if debug:
        print("Those jobs are necessary for the target jobs above: ")
        print(miniJobsNames)

    # find not removable job and their dependencies
    unRemoveableJobs = set()
    for j in jobs:
        if not isRemoveableJob(jobs[j], j):
            unRemoveableJobs = unRemoveableJobs.union(getDependencies(j, jobs))

    if debug:
        print("required UnRemoveableJobs and related dependencies: ")
        print(unRemoveableJobs)

    miniJobsNames = miniJobsNames.union(unRemoveableJobs)

    # get the target with clean matrix
    cleanedJobs = cleanMatrix(jobs, targetsDic)
    # before make change stash change before
    curCommit = getCurCommit()
    popStash = gitStash(debug)
    try:
        # replace with selectswriteback
        selectWriteBack(
            dirToYaml, miniJobsNames, cleanedJobs, yamlFiles, list(targetsDic.keys()), repeatNum
        )
        # push to gitlab
        gitAdd(dirToYaml, debug)
        gitCommit(targetJobs, debug, args.noVerify)
        gitPush(debug)
    except Exception:
        print(
            f"{Bcolors.FAIL}"
            + "Unexpected error happend! And cannot identify it (try re-enter nix-shell to have dependents update): \n"
            + f"{Bcolors.ENDC}"
        )
        traceback.print_exc()
    finally:
        try:
            if debug:
                print(
                    f"{Bcolors.WARNING}"
                    + "Recovering ci config file and stashed work ...\n"
                    + f"{Bcolors.ENDC}"
                )
            resetBack(popStash, curCommit, debug)
        except:
            stashMsg = ""
            if popStash:
                stashMsg = " and apply/pop stash."
            print(
                f"{Bcolors.FAIL}"
                + "[Error] error happend during recover, please make sure reset commit back to "
                + curCommit
                + stashMsg
                + f"{Bcolors.ENDC}"
            )
    curBranch = gitGetBranch()
    printPipelineURL(curBranch)


#  unit test for some functions
class TestScriptFunctions(unittest.TestCase):
    def testAddrepeat(self):
        PipeDumper.add_multi_representer(Tagged, represent_tagged)
        PipeLoader.add_multi_constructor("!", construct_undefined)
        dirToYaml = os.getcwd() + "/tests/"
        yamlFiles = getListOfYamlFiles(dirToYaml)
        # get minimum jobs
        jobs = getAllConfig(yamlFiles, dirToYaml)
        addRepeat(jobs, "testExample:b", 4)
        self.assertEqual(
            jobs["testExample:b"]["parallel"],
            {"matrix": [{"REPEAT": [0, 1, 2, 3]}]},
        )
        addRepeat(jobs, "testExample:c", 4)
        self.assertEqual(
            jobs["testExample:c"]["parallel"],
            {
                "matrix": [
                    {
                        "TESTFILE": [
                            "f1",
                            "f2",
                            "f3",
                            "f4",
                        ],
                        "REPEAT": [0, 1, 2, 3],
                    }
                ]
            },
        )
        addRepeat(jobs, "testExample:a", 4)
        self.assertEqual(jobs["testExample:a"]["parallel"], 4)
