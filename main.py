import unittest
from gitlab_ci_helper import gitlabCiHelper
from gitlab_ci_helper import TestScriptFunctions


if __name__ == "__main__":
    # please change this to your yaml files directory
    dirToYaml = "tests/"
    # gitlabCiHelper(dirToYaml)
    # Uncomment to run unit tests
    unittest.main()
