"""
General image classification scenario
"""

import logging
from typing import Optional

from tqdm import tqdm

from armory.utils.config_loading import (
    load_dataset,
    load_model,
    load_attack,
    load_adversarial_dataset,
    load_defense_wrapper,
    load_defense_internal,
    load_label_targeter,
)
from armory.utils import metrics
from armory.scenarios.base import Scenario

logger = logging.getLogger(__name__)


class ImageClassificationTask(Scenario):
    def _evaluate(
        self, config: dict, num_eval_batches: Optional[int], skip_benign: Optional[bool]
    ) -> dict:
        """
        Evaluate the config and return a results dict
        """

        model_config = config["model"]
        classifier, preprocessing_fn = load_model(model_config)

        defense_config = config.get("defense") or {}
        defense_type = defense_config.get("type")

        if defense_type in ["Preprocessor", "Postprocessor"]:
            logger.info(f"Applying internal {defense_type} defense to classifier")
            classifier = load_defense_internal(config["defense"], classifier)

        if model_config["fit"]:
            classifier.set_learning_phase(True)
            logger.info(
                f"Fitting model {model_config['module']}.{model_config['name']}..."
            )
            fit_kwargs = model_config["fit_kwargs"]

            logger.info(f"Loading train dataset {config['dataset']['name']}...")
            train_data = load_dataset(
                config["dataset"],
                epochs=fit_kwargs["nb_epochs"],
                split_type="train",
                preprocessing_fn=preprocessing_fn,
                shuffle_files=True,
            )
            if defense_type == "Trainer":
                logger.info(f"Training with {defense_type} defense...")
                defense = load_defense_wrapper(config["defense"], classifier)
                defense.fit_generator(train_data, **fit_kwargs)
            else:
                logger.info("Fitting classifier on clean train dataset...")
                classifier.fit_generator(train_data, **fit_kwargs)

        if defense_type == "Transform":
            # NOTE: Transform currently not supported
            logger.info(f"Transforming classifier with {defense_type} defense...")
            defense = load_defense_wrapper(config["defense"], classifier)
            classifier = defense()

        classifier.set_learning_phase(False)

        metrics_logger = metrics.MetricsLogger.from_config(
            config["metric"], skip_benign=skip_benign
        )
        if skip_benign:
            logger.info("Skipping benign classification...")
        else:
            # Evaluate the ART classifier on benign test examples
            logger.info(f"Loading test dataset {config['dataset']['name']}...")
            test_data = load_dataset(
                config["dataset"],
                epochs=1,
                split_type="test",
                preprocessing_fn=preprocessing_fn,
                num_batches=num_eval_batches,
                shuffle_files=False,
            )

            logger.info("Running inference on benign examples...")
            for x, y in tqdm(test_data, desc="Benign"):
                with metrics.resource_context(
                    name="Inference",
                    profiler=config["metric"].get("profiler_type"),
                    computational_resource_dict=metrics_logger.computational_resource_dict,
                ):
                    y_pred = classifier.predict(x)
                metrics_logger.update_task(y, y_pred)
            metrics_logger.log_task()

        # Evaluate the ART classifier on adversarial test examples
        logger.info("Generating or loading / testing adversarial examples...")

        attack_config = config["attack"]
        attack_type = attack_config.get("type")
        targeted = bool(attack_config.get("kwargs", {}).get("targeted"))
        if targeted and attack_config.get("use_label"):
            raise ValueError("Targeted attacks cannot have 'use_label'")
        if attack_type == "preloaded":
            test_data = load_adversarial_dataset(
                attack_config,
                epochs=1,
                split_type="adversarial",
                preprocessing_fn=preprocessing_fn,
                num_batches=num_eval_batches,
                shuffle_files=False,
            )
        else:
            attack = load_attack(attack_config, classifier)
            if targeted != getattr(attack, "targeted", False):
                logger.warning(
                    f"targeted config {targeted} != attack field {getattr(attack, 'targeted', False)}"
                )
            test_data = load_dataset(
                config["dataset"],
                epochs=1,
                split_type="test",
                preprocessing_fn=preprocessing_fn,
                num_batches=num_eval_batches,
                shuffle_files=False,
            )
            if targeted:
                label_targeter = load_label_targeter(attack_config["targeted_labels"])
        for x, y in tqdm(test_data, desc="Attack"):
            with metrics.resource_context(
                name="Attack",
                profiler=config["metric"].get("profiler_type"),
                computational_resource_dict=metrics_logger.computational_resource_dict,
            ):
                if attack_type == "preloaded":
                    x, x_adv = x
                    if targeted:
                        y, y_target = y
                elif attack_config.get("use_label"):
                    x_adv = attack.generate(x=x, y=y)
                elif targeted:
                    y_target = label_targeter.generate(y)
                    x_adv = attack.generate(x=x, y=y_target)
                else:
                    x_adv = attack.generate(x=x)

            y_pred_adv = classifier.predict(x_adv)
            if targeted:
                metrics_logger.update_task(y_target, y_pred_adv, adversarial=True)
            else:
                metrics_logger.update_task(y, y_pred_adv, adversarial=True)
            metrics_logger.update_perturbation(x, x_adv)
        metrics_logger.log_task(adversarial=True, targeted=targeted)
        return metrics_logger.results()
