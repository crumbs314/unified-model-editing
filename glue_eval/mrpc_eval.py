from datasets import load_metric, load_dataset
from transformers import AutoModelForCausalLM, AutoTokenizer
from sklearn.metrics import matthews_corrcoef, f1_score
from glue_eval.useful_functions import load_data
import time
import torch
import numpy as np

MAX_NUMBER_OF_FEW_SHOTS = 50

class MRPCEval():
    def __init__(self, model, tokenizer, number_of_tests = None, number_of_few_shots = 0, eval_split = 'validation'):
        assert number_of_few_shots < MAX_NUMBER_OF_FEW_SHOTS, f"The number of few shots should not exceed {number_of_few_shots}"
        self.number_of_tests = number_of_tests
        self.number_of_few_shots = number_of_few_shots
        self.model = model
        self.tokenizer = tokenizer
        self.few_shots, self.eval_dataset = load_data_split('glue_eval/dataset/mrpc.pkl', number_of_few_shots)
        self.eval_dataset = self.eval_dataset[:number_of_tests] if not (number_of_tests is None) else self.eval_dataset

        self._initialize_prompts()


    def _initialize_prompts(self):
        self.prefix_prompt = 'Are the sentences paraphrases of each other.\n'
        self.postfix_prompt = 'Answer:'
        self.few_shot_context = ""
        for _, few_shot in enumerate(self.few_shots):
            self.few_shot_context += f'{self.prefix_prompt}Sentence 1: {few_shot['sentence1']}\nSentence 2: {few_shot['sentence2']}\nAnswer: {'No' if few_shot['label'] == 0 else 'Yes'}\n'
        print("FEWWWW_SHOTTT")
        print(self.few_show_context)

    
    def _create_prompt(self, example):
        prompt = 'Sentence 1: ' + example['sentence1'] + '\n'
        prompt += 'Sentence 2: ' + example['sentence2'] + '\n'

        input_prompt = self.few_shot_context + self.prefix_prompt + prompt + self.postfix_prompt

        return input_prompt, example['sentence1'], example['sentence2'], example['label']


    def _get_answer(self, generated_text):
        answer_text = generated_text.split(self.postfix_prompt)[-1].strip().strip()

        if 'Yes' in answer_text:
            return 1
        elif 'No' in answer_text:
            return 0

        return -1


    def evaluate(self, gen_len = 3, llama = False, print_logs = False):
        yes_tok, no_tok = (self.tokenizer(f" {n}")["input_ids"] for n in ['Yes', 'No'])

        if llama:#'llama-2' in model.config._name_or_path.lower():
            yes_tok = yes_tok[2:]
            no_tok = no_tok[2:]

        yes_len, no_len = (len(n) for n in [yes_tok, no_tok])

        correct = 0
        incorrect = 0
        invalid = 0

        pos_correct = 0
        neg_correct = 0
        pos_incorrect = 0
        neg_incorrect = 0

        predictions = []
        labels = []
        predictions_new = []
        stored_generations = []
        start = time.time()

        for s, example in enumerate(self.eval_dataset):

            input_prompt, sentence1, sentence2, label = self._create_prompt(example)
            input_prompt_ids = self.tokenizer.encode(input_prompt, return_tensors='pt').to('cuda')
            input_prompt_text = self.tokenizer.decode(input_prompt_ids[0], skip_special_tokens=True)

            prefix_tok_len = len(self.tokenizer(input_prompt)["input_ids"]) - 1
            dic = {0: [yes_tok, yes_len], 1: [no_tok, no_len]}
            max_len = input_prompt_ids.shape[1] + gen_len
        
            probs = [0, 0]
            gen_texts = [0,0]

            suffixes = ['Yes', 'No']

            for i in range(2):
                prompt_tok = self.tokenizer([f"{input_prompt} {suffixes[i]}"], return_tensors="pt").to('cuda')

                with torch.no_grad():
                    logits = self.model(**prompt_tok).logits    #the model takes in a list of prompts. logits = a x b x c where a is the number of prompts. Then bxc is the output logits. 

                if True:
                    logits = logits[:, 1:, :]


                cur_len = dic[i][1]

                for j in range(cur_len):
                    cur_tok = dic[i][0][j]
                    probs[i] += -torch.nn.functional.log_softmax(
                    logits[0, prefix_tok_len + j - 1, :], dim=0
                    )[cur_tok].item()
                probs[i] /= cur_len
                
                gen_texts[i] = self.tokenizer.decode(logits[0, prefix_tok_len - 1 : prefix_tok_len + cur_len - 1, :].argmax(dim = -1))

            output = self.model.generate(input_prompt_ids,max_length = max_len, do_sample = False)
            generated_text = self.tokenizer.decode(output[0], skip_special_tokens=True)

            prob_yes = np.exp(-probs[0])
            prob_no = np.exp(-probs[1])

            gen_text1 = gen_texts[0] #gen_text1 and 2 are the same cause prompts r same
            gen_text2 = gen_texts[1]


            answer = self._get_answer(generated_text)
            predictions.append(answer)
            labels.append(label)
            predictions_new.append(1 if prob_yes > prob_no else 0)


            if answer == -1:
                invalid += 1
            else:

                if answer == label:
                    correct += 1

                    if label == 1:
                        pos_correct += 1
                    elif label == 0:
                        neg_correct += 1

                else:
                    incorrect += 1

                    if label == 1:
                        pos_incorrect += 1
                    elif label == 0:
                        neg_incorrect += 1

            exp_temp_dict = {
                'sentence1': sentence1, 
                'sentence2': sentence2, 
                'label': label,
                'input_prompt': input_prompt_text,
                'generated_text': generated_text.replace(input_prompt_text, ''),
                'answer': answer,
                'prob_yes': prob_yes,
                'prob_no': prob_no,
                'gen_text_new': gen_text1,
                'answer_new': 1 if prob_yes > prob_no else 0,
                'correct': answer == label,
                'invalid': True if answer == -1 else False
            }
            stored_generations.append(exp_temp_dict)

            if print_logs:
                mcc = matthews_corrcoef(labels, predictions)
                f1 = f1_score(labels, predictions, average='weighted')
                print(generated_text)
                print(correct, incorrect, invalid, s+1, '|', pos_correct, neg_correct, '|', pos_incorrect, neg_incorrect, '|ACC: ', correct / (correct + incorrect + invalid), '|MCC:', mcc, '|F1:', f1)
                print('--'*50)


        end = time.time()
        mcc = matthews_corrcoef(labels, predictions)
        f1 = f1_score(labels, predictions, average='weighted')
        f1_new = f1_score(labels, predictions_new, average='weighted')
        result_dict = {
            'correct': correct,
            'incorrect': incorrect,
            'invalid': invalid,
            'total': s+1,
            'f1': f1,
            'f1_new': f1_new,
            'mcc': mcc,
            'time': end-start,
        }

        return result_dict, stored_generations

if __name__ == '__main__':
    # Load the tokenizer and model
    #model_name = 'EleutherAI/gpt-j-6b'
    #model_name = 'gpt2-xl'
    model_name = '/data/akshat/lingua-models/Llama-2-7b-hf'
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    model = AutoModelForCausalLM.from_pretrained(model_name)
    model.to('cuda')

    mrpc_eval = MRPCEval(model, tokenizer)
    mrpc_eval.evaluate(print_logs='True')
    
    