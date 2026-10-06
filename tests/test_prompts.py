"""Furnished prompts preserve the specification text and profile boundaries."""

import json
from dataclasses import replace
from importlib.resources import files
from pathlib import Path

import yaml

from test_server import ServerFixture

PROMPTS = yaml.safe_load((Path(__file__).parents[1] / "spec/prompts.yaml").read_text())


class PromptTest(ServerFixture):
    def test_module_profiles_list_no_cross_module_prompts(self):
        for profile in ("evs", "cadsr"):
            with self.subTest(profile=profile):
                self.settings = replace(self.settings, profile=profile)
                result = self.session(lambda client: client.list_prompts())
                self.assertEqual(result.prompts, [])

    def test_packaged_templates_equal_the_specification(self):
        packaged = json.loads(files("nci_si_mcp").joinpath("data/prompts.json").read_text())
        self.assertEqual(packaged, PROMPTS)

    def test_unified_prompts_preserve_arguments_and_exact_substituted_text(self):
        async def interaction(client):
            listed = await client.list_prompts()
            self.assertEqual({p.name for p in listed.prompts}, set(PROMPTS))
            for prompt in listed.prompts:
                expected = PROMPTS[prompt.name]
                self.assertEqual(prompt.title, expected["title"])
                self.assertEqual(
                    [a.model_dump(exclude_none=True) for a in prompt.arguments],
                    expected["arguments"],
                )
                arguments = {a["name"]: "literal {text}\n<value>" for a in expected["arguments"]}
                result = await client.get_prompt(prompt.name, arguments)
                self.assertEqual(len(result.messages), 1)
                self.assertEqual(result.messages[0].role, "user")
                self.assertEqual(
                    result.messages[0].content.text, expected["template"].format(**arguments)
                )

        self.session(interaction)

    def test_optional_argument_is_empty_when_omitted(self):
        async def interaction(client):
            result = await client.get_prompt("uscdi_cancer_curation", {"element": "Grade"})
            self.assertEqual(
                result.messages[0].content.text,
                PROMPTS["uscdi_cancer_curation"]["template"].format(element="Grade", values=""),
            )

        self.session(interaction)
