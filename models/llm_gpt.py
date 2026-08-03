# Copyright Zixi Zhang
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

import random
import time

from models.llm_base import *
import os

import openai
import tiktoken


class ChatGPT(BaseLLM):
    def __init__(
        self,
        system_prompt: str = "",
        model_name="gpt-3.5-turbo",
        temperature=0.4,
        top_p=1,
        max_gen_tokens=600,
        best_iter_buffer_resetting: str = "STABLE",
        compress_msg_algo: str = "best 3",
        prioritise_harder_bins: bool = False,  # not needed if compress_msg_algo as "Successful Difficult Responses"
    ):
        prioritise_harder_bins = (
            prioritise_harder_bins
            or compress_msg_algo == "Successful Difficult Responses"
        )
        super().__init__(
            system_prompt, best_iter_buffer_resetting, prioritise_harder_bins
        )
        openai_api_key = os.getenv("OPENAI_API_KEY")
        openai_base_url = os.getenv("OPENAI_BASE_URL")
        assert (
            openai_api_key is not None
        ), "OPENAI_API_KEY not found. Use a real key for OpenAI or a dummy value for a local OpenAI-compatible endpoint."
        openai.api_key = openai_api_key
        if openai_base_url:
            openai.base_url = openai_base_url
        self.client = (
            openai.OpenAI(api_key=openai_api_key, base_url=openai_base_url)
            if hasattr(openai, "OpenAI")
            else None
        )
        if model_name in {"gpt-3.5-turbo", "gpt-4"}:
            self.model_name = model_name + "-0613"
            self.long_context_model_name = model_name + "-16k" + "-0613"
        else:
            self.model_name = model_name
            self.long_context_model_name = model_name
        self.model_max_context = (
            4096
            if "gpt-3.5-turbo" in model_name
            else 8192
            if "gpt-4" in model_name
            else None
        )
        self.temperature = temperature
        self.top_p = top_p
        self.max_gen_tokens = max_gen_tokens

        self.messages = []
        self.recent_msgs = []
        if self.system_prompt != "":
            self.messages.append({"role": "system", "content": self.system_prompt})

        self.compress_msg_algo: Callable[
            [], List[Dict[str, str]]
        ] = self.__resolve_msg_compress_algo(compress_msg_algo)

    def __resolve_msg_compress_algo(self, compress_msg_algo) -> Callable:
        if compress_msg_algo in [
            "best 3",
            "Successful Responses",
            "Successful Difficult Responses",
        ]:
            return self.__best_3
        elif compress_msg_algo in [
            "best 2 recent 1",
            "Mixed Recent and Successful Responses",
        ]:
            return self.__best_2_recent_1
        elif compress_msg_algo in ["recent 3", "Recent Responses"]:
            return self.__recent_3
        else:
            methods = [
                "recent 3",
                "best 3",
                "best 2 recent 1",
                "Recent Responses",
                "Mixed Recent and Successful Responses",
                "Successful Responses",
                "Successful Difficult Responses",
            ]
            raise ValueError(
                f"Invalid conversation compression algorithm {compress_msg_algo}. \\"
                f"Please use one of the following methods: {methods}"
            )

    def __str__(self):
        return self.model_name

    def _create_chat_completion(self, model):
        request = {
            "model": model,
            "messages": self.messages,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "max_tokens": self.max_gen_tokens,
            "n": 1,
        }

        if self.client is None:
            return openai.ChatCompletion.create(**request)

        try:
            return self.client.chat.completions.create(**request)
        except Exception as e:
            if (
                e.__class__.__name__ == "BadRequestError"
                and "max_tokens" in str(e)
                and "max_completion_tokens" in str(e)
            ):
                request["max_completion_tokens"] = request.pop("max_tokens")
                return self.client.chat.completions.create(**request)
            raise

    def _is_retryable_error(self, e):
        return e.__class__.__name__ in {
            "APIConnectionError",
            "APIError",
            "APITimeoutError",
            "ServiceUnavailableError",
            "Timeout",
        }

    def _extract_response(self, result):
        if isinstance(result, dict):
            message = result["choices"][0]["message"]
            content = message["content"]
            usage = result["usage"]
            input_token = usage["prompt_tokens"]
            output_token = usage["completion_tokens"]
            total_token = usage["total_tokens"]
        else:
            message = result.choices[0].message
            content = message.content
            usage = result.usage
            input_token = usage.prompt_tokens
            output_token = usage.completion_tokens
            total_token = usage.total_tokens

        return content, input_token, output_token, total_token

    def __call__(self, prompt: str) -> Tuple[str, Tuple[int, int, int]]:
        self._compress_conversation()
        self.messages.append({"role": "user", "content": prompt})
        self.recent_msgs.append({"role": "user", "content": prompt})

        token_cnt = self._check_token_num()
        if (
            self.model_max_context is None
            or token_cnt + self.max_gen_tokens <= self.model_max_context
        ):
            model = self.model_name
        else:
            model = self.long_context_model_name

        for delay in [2**x for x in range(0, 6)]:
            try:
                result = self._create_chat_completion(model)
            except Exception as e:
                if not self._is_retryable_error(e):
                    raise
                if e.__class__.__name__ == "RateLimitError":
                    randomness_collision_avoidance = (
                        random.randint(1000, 4000) / 100.0
                    )
                else:
                    randomness_collision_avoidance = random.randint(0, 1000) / 300.0
                sleep_dur = delay + randomness_collision_avoidance
                print(f"Error: {e}. Retrying in {round(sleep_dur, 2)} seconds.")
                time.sleep(sleep_dur)
                continue
            else:
                response_content, input_token, output_token, total_token = (
                    self._extract_response(result)
                )
                response_message = {
                    "role": "assistant",
                    "content": response_content,
                }
                self.messages.append(response_message)
                self.recent_msgs.append(response_message)
                self.total_msg_cnt += 1
                if total_token != input_token + output_token:
                    print(
                        f"Correcting fault of token cnts: input {input_token}, output {output_token}, "
                        f"total {total_token}"
                    )
                    total_token = input_token + output_token
                return response_content, (
                    input_token,
                    output_token,
                    total_token,
                )

    def _compress_conversation(self):
        # STABLE RST & CLEAR RST
        if (
            self.best_iter_buffer_resetting in ["STABLE", "CLEAR"]
            and len(self.messages) < 4 + 2 * ChatGPT.REMAIN_ITER_NUM
        ):
            return
        if self.messages[0]["role"] == "system":
            init = self.messages[:3]
        else:
            init = self.messages[:2]

        # Keep recent / previous successful iter messages
        self.messages = init + self.compress_msg_algo()
        return

    def __best_3(self) -> List[Dict[str, str]]:
        return self._select_successful(n_best=3)

    def __best_2_recent_1(self) -> List[Dict[str, str]]:
        best = self._select_successful(n_best=2)
        recent = self.recent_msgs[-2:]
        return best + recent

    def __recent_3(self) -> List[Dict[str, str]]:
        return self.messages[-2 * 3 :]

    def _check_token_num(self) -> int:
        return num_tokens_from_messages(self.messages, self.model_name)

    def reset(self):
        self.messages.clear()
        self.recent_msgs.clear()
        if self.system_prompt != "":
            self.messages.append({"role": "system", "content": self.system_prompt})
        # CLEAR RST
        if self.best_iter_buffer_resetting == "CLEAR":
            self.best_messages.clear()


def num_tokens_from_messages(
    messages: List[Dict[str, str]], model="gpt-3.5-turbo-0613"
) -> int:
    """Return the number of tokens used by a list of messages.

    Unknown OpenAI-compatible model names, such as local Ollama models, do not
    have exact ChatML accounting. Use a conservative cl100k_base approximation
    instead of failing before the request is sent.
    """
    try:
        encoding = tiktoken.encoding_for_model(model)
    except KeyError:
        print("Warning: model not found. Using cl100k_base encoding.")
        try:
            encoding = tiktoken.get_encoding("cl100k_base")
        except Exception:
            encoding = None
    except Exception:
        encoding = None
    if model in {
        "gpt-3.5-turbo-0613",
        "gpt-3.5-turbo-16k-0613",
        "gpt-4-0314",
        "gpt-4-32k-0314",
        "gpt-4-0613",
        "gpt-4-32k-0613",
    }:
        tokens_per_message = 3
        tokens_per_name = 1
    elif model == "gpt-3.5-turbo-0301":
        tokens_per_message = (
            4  # every message follows <|start|>{role/name}\n{content}<|end|>\n
        )
        tokens_per_name = -1  # if there's a name, the role is omitted
    elif "gpt-3.5-turbo" in model:
        # print("Warning: gpt-3.5-turbo may update over time. Returning num tokens assuming gpt-3.5-turbo-0613.")
        return num_tokens_from_messages(messages, model="gpt-3.5-turbo-0613")
    elif "gpt-4" in model:
        # print("Warning: gpt-4 may update over time. Returning num tokens assuming gpt-4-0613.")
        return num_tokens_from_messages(messages, model="gpt-4-0613")
    else:
        tokens_per_message = 3
        tokens_per_name = 1
    num_tokens = 0
    for message in messages:
        num_tokens += tokens_per_message
        for key, value in message.items():
            if encoding is None:
                num_tokens += max(1, len(value) // 4)
            else:
                num_tokens += len(encoding.encode(value))
            if key == "name":
                num_tokens += tokens_per_name
    num_tokens += 3  # every reply is primed with <|start|>assistant<|message|>
    return num_tokens
